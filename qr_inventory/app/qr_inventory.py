import time
import json
import os
import logging
import subprocess
import threading
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('qr_inventory')

# ------------------------------------------------------------
# Load add-on options
# ------------------------------------------------------------
opts_path = '/data/options.json'
if os.path.exists(opts_path):
    with open(opts_path, 'r', encoding='utf-8') as f:
        opts = json.load(f)
else:
    logger.warning('options.json not found, using defaults')
    opts = {}

def _opt_int(key, default):
    try:
        return int(opts.get(key, default))
    except Exception:
        return default

def _opt_float(key, default):
    try:
        return float(opts.get(key, default))
    except Exception:
        return default

def _opt_bool(key, default):
    v = opts.get(key, default)
    return bool(v)

def _opt_str(key, default):
    v = opts.get(key, default)
    return default if v is None else str(v)

interval = _opt_int('interval_seconds', 60)
required = _opt_int('required_consistency', 3)
camera_mode = _opt_str('camera_mode', 'rtsps').lower()
stream_url = opts.get('rtsp_url')
tls_verify = _opt_bool('tls_verify', False)

# ------------------------------------------------------------
# Accuracy tuning (small QR improvements)
# ------------------------------------------------------------
zone_fallback = _opt_bool("zone_fallback", True)
use_preprocess = _opt_bool("use_preprocess", True)
roi_padding_px = _opt_int("roi_padding_px", 60)

# Backwards compat (single scale)
roi_scale = _opt_float("roi_scale", 2.0)

# New: multiple ROI scales (comma string or list), e.g. "2.0,3.0,4.0"
roi_scales_raw = opts.get("roi_scales", None)

# New: how much to upscale a perspective-warped candidate patch
warp_scale = _opt_float("warp_scale", 5.0)

# New: try inverted images too
try_invert = _opt_bool("try_invert", True)

# New: cap candidates to avoid CPU spikes
max_candidates = _opt_int("max_candidates", 160)

# New: adaptive threshold block sizes (odd numbers), e.g. "21,35,51"
abs_raw = opts.get("adaptive_block_sizes", None)
if isinstance(abs_raw, list):
    adaptive_block_sizes = [int(x) for x in abs_raw if int(x) % 2 == 1 and int(x) >= 11]
elif isinstance(abs_raw, str) and abs_raw.strip():
    adaptive_block_sizes = []
    for part in abs_raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part)
            if v % 2 == 1 and v >= 11:
                adaptive_block_sizes.append(v)
        except Exception:
            pass
    if not adaptive_block_sizes:
        adaptive_block_sizes = [35]
else:
    adaptive_block_sizes = [35]

def _parse_roi_scales(raw, fallback):
    if raw is None:
        return [fallback] if fallback and fallback > 0 else [2.0]
    if isinstance(raw, list):
        out = []
        for x in raw:
            try:
                xf = float(x)
                if xf > 0:
                    out.append(xf)
            except Exception:
                pass
        return out or ([fallback] if fallback and fallback > 0 else [2.0])
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        out = []
        for p in parts:
            try:
                xf = float(p)
                if xf > 0:
                    out.append(xf)
            except Exception:
                pass
        return out or ([fallback] if fallback and fallback > 0 else [2.0])
    return [fallback] if fallback and fallback > 0 else [2.0]

ROI_SCALES = _parse_roi_scales(roi_scales_raw, roi_scale)

# ------------------------------------------------------------
# Debug / diagnostics options
# ------------------------------------------------------------
debug_metrics = _opt_bool("debug_metrics", False)          # logs candidate diagnostics + certainty
debug_log_every = max(1, _opt_int("debug_log_every", 1))   # every N cycles
debug_max_failed_logs = max(0, _opt_int("debug_max_failed_logs", 20))

debug_save_failed = _opt_bool("debug_save_failed", False)       # save warped candidates that fail decoding
debug_save_decoded = _opt_bool("debug_save_decoded", False)     # save warped candidates that decode
debug_max_images_per_cycle = max(0, _opt_int("debug_max_images_per_cycle", 20))
debug_image_format = _opt_str("debug_image_format", "jpg").lower().strip()
if debug_image_format not in ("jpg", "jpeg", "png"):
    debug_image_format = "jpg"
debug_jpeg_quality = int(max(20, min(95, _opt_int("debug_jpeg_quality", 85))))
debug_dir = _opt_str("debug_dir", "/data/debug").strip() or "/data/debug"

stream_info_interval_minutes = max(0, _opt_int("stream_info_interval_minutes", 0))  # 0=only startup

# ------------------------------------------------------------
# Overlay PNG name (configurable)
# ------------------------------------------------------------
_overlay_name = _opt_str('overlay_png_name', 'overlay.png')
_overlay_name = _overlay_name.strip().lstrip('/')
_overlay_name = os.path.basename(_overlay_name)  # prevent nested paths / traversal
if not _overlay_name:
    _overlay_name = 'overlay.png'
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += '.png'
OVERLAY_PNG_NAME = _overlay_name

# HTTP server binds to a FIXED internal container port.
HTTP_PORT = 8099

# ------------------------------------------------------------
# Zones
# ------------------------------------------------------------
zones_raw = opts.get('zones', {})
if isinstance(zones_raw, str):
    try:
        zones = json.loads(zones_raw) if zones_raw.strip() else {}
    except Exception:
        logger.warning('zones is not valid JSON, using {}')
        zones = {}
elif isinstance(zones_raw, dict):
    zones = zones_raw
else:
    zones = {}

def centroid_to_zone(cx, cy, zones_dict):
    for name, box in zones_dict.items():
        try:
            x1, y1, x2, y2 = box
        except Exception:
            continue
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return name
    return None

# ------------------------------------------------------------
# State + persistence
# ------------------------------------------------------------
history_maxlen = required if required and required > 0 else 1
history = defaultdict(lambda: deque(maxlen=history_maxlen))
confirmed = {}

qcd = cv2.QRCodeDetector()

# ------------------------------------------------------------
# Stream info (ffprobe)
# ------------------------------------------------------------
def _run_ffprobe(url: str):
    """
    Return dict with best-effort stream metadata or None.
    """
    # Some ffprobe builds support -tls_verify; include if rtsps and verify disabled
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-select_streams", "v:0"]

    if url.lower().startswith("rtsps://") and not tls_verify:
        cmd += ["-tls_verify", "0"]

    cmd += [url]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception as e:
        logger.debug("ffprobe failed: %s", e)
        return None

    if proc.returncode != 0 or not proc.stdout:
        return None

    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="ignore"))
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        out = {
            "codec_name": s.get("codec_name"),
            "codec_long_name": s.get("codec_long_name"),
            "profile": s.get("profile"),
            "pix_fmt": s.get("pix_fmt"),
            "width": s.get("width"),
            "height": s.get("height"),
            "bit_rate": s.get("bit_rate"),
            "avg_frame_rate": s.get("avg_frame_rate"),
            "r_frame_rate": s.get("r_frame_rate"),
        }
        return out
    except Exception:
        return None

def _log_stream_info(tag: str):
    if not stream_url:
        return
    info = _run_ffprobe(stream_url)
    if not info:
        logger.info("%s: Stream info: (ffprobe unavailable or no metadata)", tag)
        return

    def _fmt_rate(r):
        if not r or r == "0/0":
            return None
        try:
            a, b = r.split("/")
            a = float(a); b = float(b)
            if b == 0:
                return None
            return round(a / b, 3)
        except Exception:
            return r

    fps = _fmt_rate(info.get("avg_frame_rate")) or _fmt_rate(info.get("r_frame_rate"))
    br = info.get("bit_rate")
    try:
        br_kbps = round(int(br) / 1000) if br else None
    except Exception:
        br_kbps = None

    logger.info(
        "%s: Stream info: codec=%s profile=%s pix_fmt=%s size=%sx%s fps=%s bitrate_kbps=%s",
        tag,
        info.get("codec_name"),
        info.get("profile"),
        info.get("pix_fmt"),
        info.get("width"),
        info.get("height"),
        fps,
        br_kbps,
    )

# ------------------------------------------------------------
# Frame capture via ffmpeg
# ------------------------------------------------------------
def get_frame_ffmpeg(url: str):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
    ]

    if url.lower().startswith("rtsps://") and not tls_verify:
        cmd += ["-tls_verify", "0"]

    cmd += [
        "-i", url,
        "-an",
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1",
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
        )
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout while reading stream")
        return None

    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="ignore").strip()
        logger.error("ffmpeg failed (rc=%s): %s", proc.returncode, err)
        return None

    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if frame is None:
        logger.error("Could not decode frame from ffmpeg output")
    return frame

# ------------------------------------------------------------
# Inventory persistence
# ------------------------------------------------------------
inv_path = '/data/inventory.json'
if os.path.exists(inv_path):
    try:
        with open(inv_path, 'r', encoding='utf-8') as f:
            confirmed = json.load(f)
    except Exception:
        confirmed = {}

def persist_mapping(payload, zone):
    prev = confirmed.get(payload)
    if prev == zone:
        return
    confirmed[payload] = zone
    try:
        with open(inv_path, 'w', encoding='utf-8') as f:
            json.dump(confirmed, f, indent=2, ensure_ascii=False)
        logger.info('Persisted mapping %s -> %s', payload, zone)
    except Exception as e:
        logger.exception('Failed writing inventory.json: %s', e)

# ------------------------------------------------------------
# Robust QR detection + diagnostics
# ------------------------------------------------------------
def _quad_area(pts: np.ndarray) -> float:
    try:
        return abs(cv2.contourArea(pts.astype(np.float32)))
    except Exception:
        return 0.0

def _max_edge_len(pts: np.ndarray) -> float:
    try:
        d = []
        for i in range(4):
            p1 = pts[i]
            p2 = pts[(i + 1) % 4]
            d.append(float(np.linalg.norm(p1 - p2)))
        return max(d) if d else 0.0
    except Exception:
        return 0.0

def _laplacian_var(gray: np.ndarray) -> float:
    try:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0

def _contrast_std(gray: np.ndarray) -> float:
    try:
        return float(np.std(gray))
    except Exception:
        return 0.0

def _certainty_score(edge_px: float, lap_var: float, contrast: float) -> float:
    """
    Heuristic 0..1 certainty score based on:
      - apparent size (edge length)
      - sharpness (laplacian variance)
      - contrast (std dev)
    """
    # Tuned for IR-ish images; feel free to tweak later.
    s_size = min(1.0, edge_px / 90.0)
    s_sharp = min(1.0, lap_var / 180.0)
    s_con = min(1.0, contrast / 45.0)
    score = 0.45 * s_size + 0.40 * s_sharp + 0.15 * s_con
    return float(max(0.0, min(1.0, score)))

def _failure_reason(edge_px: float, lap_var: float, contrast: float) -> str:
    if edge_px < 35:
        return "too_small"
    if lap_var < 40:
        return "blurry"
    if contrast < 18:
        return "low_contrast"
    return "unknown"

def _detect_and_decode_multi(img):
    decoded = []
    candidates = []
    try:
        retval, decoded_info, points, _ = qcd.detectAndDecodeMulti(img)
    except Exception:
        return decoded, candidates

    if not retval or points is None:
        return decoded, candidates

    if decoded_info is None:
        decoded_info = [""] * len(points)

    for payload, pts in zip(decoded_info, points):
        if pts is None:
            continue
        pts = np.array(pts, dtype=np.float32).reshape(-1, 2)
        if pts.shape[0] != 4:
            continue
        payload = (payload or "").strip()
        if payload:
            decoded.append((payload, pts))
        else:
            candidates.append(pts)

    return decoded, candidates

def _detect_points(img):
    try:
        ok, points = qcd.detectMulti(img)
    except Exception:
        return []
    if not ok or points is None:
        return []
    out = []
    for pts in points:
        if pts is None:
            continue
        pts = np.array(pts, dtype=np.float32).reshape(-1, 2)
        if pts.shape[0] == 4:
            out.append(pts)
    return out

def _preprocess_gray_variants(gray: np.ndarray):
    yield gray
    if try_invert:
        yield 255 - gray

    if not use_preprocess:
        return

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    yield clahe
    if try_invert:
        yield 255 - clahe

    blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
    sharp = cv2.addWeighted(clahe, 1.8, blur, -0.8, 0)
    yield sharp
    if try_invert:
        yield 255 - sharp

    for bs in adaptive_block_sizes:
        thr = cv2.adaptiveThreshold(
            sharp, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            int(bs), 5
        )
        yield thr
        if try_invert:
            yield 255 - thr

    _, otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield otsu
    if try_invert:
        yield 255 - otsu

def _ensure_debug_dir():
    try:
        os.makedirs(debug_dir, exist_ok=True)
    except Exception:
        pass

def _save_debug_image(prefix: str, img_gray: np.ndarray):
    """
    Saves img to /data/debug/ with timestamp if enabled.
    """
    if not img_gray.size:
        return
    _ensure_debug_dir()
    ts = time.strftime("%Y%m%d-%H%M%S")
    fn = f"{prefix}_{ts}.{debug_image_format}"
    path = os.path.join(debug_dir, fn)

    try:
        if debug_image_format in ("jpg", "jpeg"):
            cv2.imwrite(path, img_gray, [cv2.IMWRITE_JPEG_QUALITY, debug_jpeg_quality])
        else:
            cv2.imwrite(path, img_gray, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    except Exception:
        pass

def _warp_patch(frame_gray: np.ndarray, pts_full: np.ndarray):
    """
    Warp the quad to a square (for decode attempts + diagnostics).
    Returns (warp_gray, size, edge_px).
    """
    h, w = frame_gray.shape[:2]
    pts = pts_full.astype(np.float32).copy()
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)

    edge = _max_edge_len(pts)
    if edge <= 5:
        return None, 0, edge

    size = int(max(160, min(2400, edge * float(warp_scale))))
    dst = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32)

    try:
        M = cv2.getPerspectiveTransform(pts, dst)
        warp = cv2.warpPerspective(frame_gray, M, (size, size), flags=cv2.INTER_CUBIC)
        return warp, size, edge
    except Exception:
        return None, 0, edge

def _decode_warp_variants(warp: np.ndarray):
    """
    Try decode on multiple variants of the warped patch.
    Returns payload or "".
    """
    for v in _preprocess_gray_variants(warp):
        try:
            payload, _, _ = qcd.detectAndDecode(v)
        except Exception:
            payload = ""
        payload = (payload or "").strip()
        if payload:
            return payload
    return ""

def detect_qr_robust(frame_bgr: np.ndarray, zones_dict: dict, cycle_idx: int):
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    h, w = frame_gray.shape[:2]

    best = {}   # payload -> {"pts": pts, "area": area, "score": score, "diag": diag}
    cand = {}   # key bucket -> {"pts": pts, "area": area}

    def add_best(payload, pts, score=None, diag=None):
        area = _quad_area(pts)
        prev = best.get(payload)
        if prev is None or area > prev["area"]:
            best[payload] = {"pts": pts, "area": area, "score": score, "diag": diag}

    def add_candidate(pts):
        cx = float(np.mean(pts[:, 0]))
        cy = float(np.mean(pts[:, 1]))
        key = (int(cx // 20), int(cy // 20))  # 20px buckets
        area = _quad_area(pts)
        prev = cand.get(key)
        if prev is None or area > prev["area"]:
            cand[key] = {"pts": pts, "area": area}

    # Pass 1: full-frame variants
    for variant in _preprocess_gray_variants(frame_gray):
        decoded, candidates = _detect_and_decode_multi(variant)
        for payload, pts in decoded:
            # diagnostics based on warped patch (best effort)
            warp, _, edge = _warp_patch(frame_gray, pts)
            if warp is not None:
                lap = _laplacian_var(warp)
                con = _contrast_std(warp)
                score = _certainty_score(edge, lap, con)
                diag = {"edge_px": edge, "lap_var": lap, "contrast": con}
            else:
                score = None
                diag = None
            add_best(payload, pts, score=score, diag=diag)

        for pts in candidates:
            add_candidate(pts)

        for pts in _detect_points(variant):
            add_candidate(pts)

    # Pass 2: per-zone candidate harvesting (scaled)
    if zone_fallback and isinstance(zones_dict, dict) and zones_dict:
        pad = max(0, int(roi_padding_px))
        for _, box in zones_dict.items():
            try:
                x1, y1, x2, y2 = map(int, box)
            except Exception:
                continue

            x1p = max(0, x1 - pad)
            y1p = max(0, y1 - pad)
            x2p = min(w - 1, x2 + pad)
            y2p = min(h - 1, y2 + pad)
            if x2p <= x1p or y2p <= y1p:
                continue

            roi = frame_gray[y1p:y2p, x1p:x2p]
            if roi.size == 0:
                continue

            for sc in ROI_SCALES:
                sc = float(sc) if sc else 1.0
                if sc <= 0:
                    sc = 1.0

                roi_s = cv2.resize(roi, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc != 1.0 else roi

                for pts in _detect_points(roi_s):
                    pts_full = (pts / sc) + np.array([x1p, y1p], dtype=np.float32)
                    add_candidate(pts_full)

                decoded, candidates = _detect_and_decode_multi(roi_s)
                for payload, pts in decoded:
                    pts_full = (pts / sc) + np.array([x1p, y1p], dtype=np.float32)
                    warp, _, edge = _warp_patch(frame_gray, pts_full)
                    if warp is not None:
                        lap = _laplacian_var(warp)
                        con = _contrast_std(warp)
                        score = _certainty_score(edge, lap, con)
                        diag = {"edge_px": edge, "lap_var": lap, "contrast": con}
                    else:
                        score = None
                        diag = None
                    add_best(payload, pts_full, score=score, diag=diag)

                for pts in candidates:
                    pts_full = (pts / sc) + np.array([x1p, y1p], dtype=np.float32)
                    add_candidate(pts_full)

    # Limit candidates
    cand_items = sorted(cand.values(), key=lambda v: v["area"], reverse=True)[:max_candidates]

    # Pass 3: warp-decode candidates + diagnostics
    failed_logged = 0
    saved_images = 0

    do_debug_this_cycle = debug_metrics and (cycle_idx % debug_log_every == 0)

    for item in cand_items:
        pts = item["pts"]
        warp, size, edge = _warp_patch(frame_gray, pts)
        if warp is None:
            continue

        lap = _laplacian_var(warp)
        con = _contrast_std(warp)
        score = _certainty_score(edge, lap, con)
        reason = _failure_reason(edge, lap, con)

        payload = _decode_warp_variants(warp)
        if payload:
            add_best(payload, pts, score=score, diag={"edge_px": edge, "lap_var": lap, "contrast": con, "warp_size": size})
            if do_debug_this_cycle and debug_save_decoded and saved_images < debug_max_images_per_cycle:
                _save_debug_image(f"decoded_{payload.replace(' ', '_')[:24]}", warp)
                saved_images += 1
        else:
            if do_debug_this_cycle and failed_logged < debug_max_failed_logs:
                cx = int(np.mean(pts[:, 0]))
                cy = int(np.mean(pts[:, 1]))
                logger.debug(
                    "QR candidate not decoded: center=(%d,%d) edge_px=%.1f warp=%d lap_var=%.1f contrast=%.1f score=%.2f reason=%s",
                    cx, cy, edge, size, lap, con, score, reason
                )
                failed_logged += 1

            if do_debug_this_cycle and debug_save_failed and saved_images < debug_max_images_per_cycle:
                _save_debug_image(f"failed_{reason}", warp)
                saved_images += 1

    # Build output
    detections = []
    for payload, v in best.items():
        pts = v["pts"]
        cx = int(np.mean(pts[:, 0]))
        cy = int(np.mean(pts[:, 1]))
        zone = centroid_to_zone(cx, cy, zones_dict)

        detections.append({
            "payload": payload,
            "points": pts.tolist(),
            "centroid": [cx, cy],
            "zone": zone,
            "score": v.get("score"),
            "diag": v.get("diag"),
        })

    return detections

# ------------------------------------------------------------
# Overlay HTTP server
# ------------------------------------------------------------
STATE_LOCK = threading.Lock()
STATE = {
    "ts": 0,
    "frame_png": None,
    "overlay_png": None,
    "detections": [],
    "last_frame_info": {},
}

def _encode_png(img):
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes() if ok else None

def _safe_label(s: str, max_len: int = 96) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s

def draw_overlay(frame, detections, zones_dict):
    out = frame.copy()
    h, w = out.shape[:2]

    ORANGE = (0, 165, 255)
    if isinstance(zones_dict, dict) and zones_dict:
        for zname, box in zones_dict.items():
            try:
                x1, y1, x2, y2 = map(int, box)
            except Exception:
                continue

            x1 = max(0, min(x1, w - 1))
            x2 = max(0, min(x2, w - 1))
            y1 = max(0, min(y1, h - 1))
            y2 = max(0, min(y2, h - 1))
            if x2 <= x1 or y2 <= y1:
                continue

            cv2.rectangle(out, (x1, y1), (x2, y2), ORANGE, 2)

            label = _safe_label(str(zname), max_len=32)
            if label:
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.55
                thickness = 2
                (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
                pad = 3

                lx1, ly1 = x1, y1
                lx2 = min(w - 1, x1 + tw + pad * 2)
                ly2 = min(h - 1, y1 + th + baseline + pad * 2)

                cv2.rectangle(out, (lx1, ly1), (lx2, ly2), ORANGE, -1)
                cv2.putText(
                    out, label,
                    (x1 + pad, min(h - 1, y1 + th + pad)),
                    font, font_scale,
                    (255, 255, 255),
                    thickness, cv2.LINE_AA
                )

    # QR codes
    for det in detections:
        pts_list = det.get("points", [])
        if not pts_list:
            continue
        pts = np.array(pts_list, dtype=np.int32).reshape((-1, 1, 2))
        if pts.size == 0:
            continue

        cv2.polylines(out, [pts], isClosed=True, color=(0, 0, 255), thickness=2)

        payload = det.get("payload", "")
        score = det.get("score", None)

        label = payload
        if debug_metrics and score is not None:
            label = f"{payload}  ({score:.2f})"

        label = _safe_label(label)
        if not label:
            continue

        x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        pad = 4

        x1 = max(0, x)
        y1 = max(0, y - th - baseline - pad * 2)
        x2 = min(w - 1, x + tw + pad * 2)
        y2 = min(h - 1, y)

        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), -1)
        cv2.putText(
            out,
            label,
            (x + pad, max(0, y - pad)),
            font,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

    return out

class OverlayHandler(BaseHTTPRequestHandler):
    def _send(self, code, content_type, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlparse(self.path).path or "/")

        with STATE_LOCK:
            overlay_png = STATE["overlay_png"]
            frame_png = STATE["frame_png"]
            det = STATE["detections"]
            ts = STATE["ts"]
            fi = STATE.get("last_frame_info", {}) or {}

        if path in ("/", "/index.html"):
            html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>QR Inventory Overlay</title></head>
<body style="font-family: sans-serif">
  <h3>QR Inventory Overlay</h3>
  <p>Overlay PNG: <code>/{OVERLAY_PNG_NAME}</code> (alias: <code>/overlay.png</code>)</p>
  <p>Last update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}</p>
  <p>Last frame: {fi}</p>
  <ul>
    <li><a href="/{OVERLAY_PNG_NAME}">/{OVERLAY_PNG_NAME}</a></li>
    <li><a href="/overlay.png">/overlay.png</a></li>
    <li><a href="/frame.png">/frame.png</a></li>
    <li><a href="/detections.json">/detections.json</a></li>
  </ul>
  <img src="/{OVERLAY_PNG_NAME}" style="max-width: 100%; height: auto;" />
</body></html>""".encode("utf-8")
            return self._send(200, "text/html; charset=utf-8", html)

        if path == "/overlay.png" or path == f"/{OVERLAY_PNG_NAME}":
            if overlay_png is None:
                return self._send(503, "text/plain; charset=utf-8", b"overlay not ready")
            return self._send(200, "image/png", overlay_png)

        if path == "/frame.png":
            if frame_png is None:
                return self._send(503, "text/plain; charset=utf-8", b"frame not ready")
            return self._send(200, "image/png", frame_png)

        if path == "/detections.json":
            body = json.dumps({"ts": ts, "detections": det, "frame_info": fi}, ensure_ascii=False).encode("utf-8")
            return self._send(200, "application/json; charset=utf-8", body)

        return self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, fmt, *args):
        logger.debug("HTTP: " + fmt, *args)

def start_http_server():
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), OverlayHandler)
        logger.info("Overlay HTTP server listening on :%d (/%s)", HTTP_PORT, OVERLAY_PNG_NAME)
        httpd.serve_forever()
    except Exception as e:
        logger.exception("Overlay HTTP server failed to start: %s", e)

threading.Thread(target=start_http_server, daemon=True).start()

# ------------------------------------------------------------
# Main loop
# ------------------------------------------------------------
logger.info(
    "Starting QR Inventory (mode=%s interval=%ss required=%s tls_verify=%s overlay_png_name=%s "
    "zone_fallback=%s use_preprocess=%s roi_padding_px=%s ROI_SCALES=%s warp_scale=%s try_invert=%s "
    "adaptive_block_sizes=%s max_candidates=%s debug_metrics=%s debug_save_failed=%s debug_save_decoded=%s debug_dir=%s)",
    camera_mode, interval, required, tls_verify, OVERLAY_PNG_NAME,
    zone_fallback, use_preprocess, roi_padding_px, ROI_SCALES, warp_scale, try_invert,
    adaptive_block_sizes, max_candidates,
    debug_metrics, debug_save_failed, debug_save_decoded, debug_dir
)

# One-time stream info at startup
_log_stream_info("STARTUP")

cycle_idx = 0
last_stream_info_ts = 0

while True:
    cycle_idx += 1
    try:
        if camera_mode not in ("rtsp", "rtsps"):
            logger.error('Unsupported camera_mode=%s (use rtsp or rtsps)', camera_mode)
            time.sleep(interval)
            continue

        if not stream_url:
            logger.error('rtsp_url is empty. Please set it in the add-on options.')
            time.sleep(interval)
            continue

        # Periodic stream info
        if stream_info_interval_minutes > 0:
            now = time.time()
            if last_stream_info_ts == 0 or (now - last_stream_info_ts) >= stream_info_interval_minutes * 60:
                _log_stream_info("PERIODIC")
                last_stream_info_ts = now

        frame = get_frame_ffmpeg(stream_url)
        if frame is None:
            time.sleep(interval)
            continue

        # Capture/output quality logging
        fh, fw = frame.shape[:2]
        if debug_metrics and (cycle_idx % debug_log_every == 0):
            logger.debug("Captured frame: %dx%d", fw, fh)

        detections = detect_qr_robust(frame, zones, cycle_idx=cycle_idx)

        for det in detections:
            info = det["payload"]
            cx, cy = det["centroid"]
            zone = det["zone"]
            score = det.get("score", None)
            diag = det.get("diag", None)

            history[info].append(zone)

            if debug_metrics and score is not None and diag is not None:
                logger.info(
                    "Detected payload=%s centroid=(%d,%d) zone=%s score=%.2f edge_px=%.1f lap_var=%.1f contrast=%.1f history=%s",
                    info, cx, cy, zone, score,
                    float(diag.get("edge_px", 0.0)),
                    float(diag.get("lap_var", 0.0)),
                    float(diag.get("contrast", 0.0)),
                    list(history[info]),
                )
            else:
                logger.info(
                    "Detected payload=%s centroid=(%d,%d) zone=%s history=%s",
                    info, cx, cy, zone, list(history[info])
                )

            if len(history[info]) >= history_maxlen and len(set(history[info])) == 1:
                confirmed_zone = history[info][-1]
                if confirmed_zone is not None:
                    persist_mapping(info, confirmed_zone)

        overlay = draw_overlay(frame, detections, zones)
        frame_png = _encode_png(frame)
        overlay_png = _encode_png(overlay)

        fi = {
            "captured_w": fw,
            "captured_h": fh,
            "frame_png_bytes": len(frame_png) if frame_png else None,
            "overlay_png_bytes": len(overlay_png) if overlay_png else None,
            "detections": len(detections),
        }

        if debug_metrics and (cycle_idx % debug_log_every == 0):
            logger.debug("Output: frame_png_bytes=%s overlay_png_bytes=%s detections=%s",
                         fi["frame_png_bytes"], fi["overlay_png_bytes"], fi["detections"])

        with STATE_LOCK:
            STATE["ts"] = int(time.time())
            STATE["frame_png"] = frame_png
            STATE["overlay_png"] = overlay_png
            STATE["detections"] = detections
            STATE["last_frame_info"] = fi

    except Exception as e:
        logger.exception("Error in main loop: %s", e)

    time.sleep(interval)