import time
import json
import os
import logging
import subprocess
import threading
import platform
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import cv2
import numpy as np

# Optional ZBar fallback (recommended on Alpine/musl)
try:
    from pyzbar.pyzbar import decode as zbar_decode
    _ZBAR_OK = True
except Exception:
    zbar_decode = None
    _ZBAR_OK = False

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
    return bool(opts.get(key, default))

def _opt_str(key, default):
    v = opts.get(key, default)
    return default if v is None else str(v)

interval = _opt_int('interval_seconds', 60)
required = _opt_int('required_consistency', 3)
camera_mode = _opt_str('camera_mode', 'rtsps').lower()
stream_url = opts.get('rtsp_url')
tls_verify = _opt_bool('tls_verify', False)

# Only scan inside zones
restrict_to_zones = _opt_bool("restrict_to_zones", False)

# Zone scan tuning
use_preprocess = _opt_bool("use_preprocess", True)
try_invert = _opt_bool("try_invert", True)
roi_padding_px = _opt_int("roi_padding_px", 60)

roi_scale = _opt_float("roi_scale", 2.0)  # backward compat
roi_scales_raw = opts.get("roi_scales", None)
zone_extra_scales_raw = opts.get("zone_extra_scales", "6.0,8.0,10.0")
zone_max_scaled_dim = max(400, _opt_int("zone_max_scaled_dim", 2400))  # prevent insane scaled ROIs

warp_scale = _opt_float("warp_scale", 5.0)  # for certainty + size estimation warp
zone_early_stop_score = _opt_float("zone_early_stop_score", 0.85)

# Decoders
enable_zbar = _opt_bool("enable_zbar", True)
opencv_qr_enabled = _opt_bool("opencv_qr_enabled", True)  # OpenCV QRCodeDetector
force_opencv_qr_on_musl = _opt_bool("force_opencv_qr_on_musl", False)

# Overlay options
overlay_show_scores = _opt_bool("overlay_show_scores", True)
overlay_show_size_px = _opt_bool("overlay_show_size_px", True)
overlay_show_candidate_reason = _opt_bool("overlay_show_candidate_reason", True)
overlay_show_zone_status = _opt_bool("overlay_show_zone_status", True)

# Candidate overlays (only available if OpenCV QR is enabled)
overlay_show_candidates = _opt_bool("overlay_show_candidates", True)
overlay_max_candidates = max(0, _opt_int("overlay_max_candidates", 40))

# Diagnostics
debug_metrics = _opt_bool("debug_metrics", False)
debug_log_every = max(1, _opt_int("debug_log_every", 1))
stream_info_interval_minutes = max(0, _opt_int("stream_info_interval_minutes", 0))

# Adaptive threshold sizes
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

def _parse_float_list(raw, fallback_list):
    if raw is None:
        return fallback_list[:]
    if isinstance(raw, list):
        out = []
        for x in raw:
            try:
                xf = float(x)
                if xf > 0:
                    out.append(xf)
            except Exception:
                pass
        return out or fallback_list[:]
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
        return out or fallback_list[:]
    return fallback_list[:]

def _dedupe_sorted(vals):
    seen = set()
    out = []
    for v in vals:
        k = round(float(v), 6)
        if k not in seen:
            seen.add(k)
            out.append(float(v))
    return sorted(out)

ROI_SCALES = _dedupe_sorted(_parse_float_list(roi_scales_raw, [roi_scale if roi_scale > 0 else 2.0]))
ZONE_EXTRA_SCALES = _dedupe_sorted(_parse_float_list(zone_extra_scales_raw, [6.0, 8.0, 10.0]))

# ------------------------------------------------------------
# Overlay PNG name (configurable)
# ------------------------------------------------------------
_overlay_name = _opt_str('overlay_png_name', 'overlay.png')
_overlay_name = _overlay_name.strip().lstrip('/')
_overlay_name = os.path.basename(_overlay_name)
if not _overlay_name:
    _overlay_name = 'overlay.png'
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += '.png'
OVERLAY_PNG_NAME = _overlay_name

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
    if not isinstance(zones_dict, dict):
        return None
    for name, box in zones_dict.items():
        try:
            x1, y1, x2, y2 = map(int, box)
        except Exception:
            continue
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return name
    return None

# ------------------------------------------------------------
# Persistence
# ------------------------------------------------------------
history_maxlen = required if required and required > 0 else 1
history = defaultdict(lambda: deque(maxlen=history_maxlen))
confirmed = {}

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
# Stream info
# ------------------------------------------------------------
def _run_ffprobe(url: str):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-select_streams", "v:0"]
    if url.lower().startswith("rtsps://") and not tls_verify:
        cmd += ["-tls_verify", "0"]
    cmd += [url]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="ignore"))
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        return {
            "codec_name": s.get("codec_name"),
            "profile": s.get("profile"),
            "pix_fmt": s.get("pix_fmt"),
            "width": s.get("width"),
            "height": s.get("height"),
            "bit_rate": s.get("bit_rate"),
            "avg_frame_rate": s.get("avg_frame_rate"),
            "r_frame_rate": s.get("r_frame_rate"),
        }
    except Exception:
        return None

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

def _log_stream_info(tag: str):
    if not stream_url:
        return
    info = _run_ffprobe(stream_url)
    if not info:
        logger.info("%s: Stream info: (ffprobe unavailable or no metadata)", tag)
        return
    fps = _fmt_rate(info.get("avg_frame_rate")) or _fmt_rate(info.get("r_frame_rate"))
    br = info.get("bit_rate")
    try:
        br_kbps = round(int(br) / 1000) if br else None
    except Exception:
        br_kbps = None
    logger.info(
        "%s: Stream info: codec=%s profile=%s pix_fmt=%s size=%sx%s fps=%s bitrate_kbps=%s",
        tag, info.get("codec_name"), info.get("profile"), info.get("pix_fmt"),
        info.get("width"), info.get("height"), fps, br_kbps
    )

# ------------------------------------------------------------
# Frame capture
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
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
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
# Metrics / heuristics
# ------------------------------------------------------------
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

def _clip_fractions(gray: np.ndarray):
    try:
        bright = float(np.mean(gray >= 250))
        dark = float(np.mean(gray <= 5))
        return bright, dark
    except Exception:
        return 0.0, 0.0

def _failure_reason_no_points(zone_w: int, zone_h: int, lap_var: float, contrast: float, bright_clip: float, dark_clip: float) -> str:
    # crude but useful for "no detection at all"
    if bright_clip >= 0.35:
        return "reflection"
    if lap_var < 40:
        return "blurry"
    if contrast < 18:
        return "low_contrast"
    if dark_clip >= 0.35:
        return "shadow"
    if min(zone_w, zone_h) < 55:
        return "too_small"
    return "unknown"

def _edge_px_from_quad(pts: np.ndarray) -> float:
    pts = np.array(pts, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        return 0.0
    d = []
    for i in range(4):
        d.append(float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])))
    return max(d) if d else 0.0

def _certainty_score(edge_px: float, lap_var: float, contrast: float) -> float:
    s_size = min(1.0, edge_px / 90.0)
    s_sharp = min(1.0, lap_var / 180.0)
    s_con = min(1.0, contrast / 45.0)
    score = 0.45 * s_size + 0.40 * s_sharp + 0.15 * s_con
    return float(max(0.0, min(1.0, score)))

def _pct(score):
    try:
        if score is None:
            return None
        return int(round(float(score) * 100))
    except Exception:
        return None

# ------------------------------------------------------------
# Preprocess variants (for ZBar and optional OpenCV QR)
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# ZBar decode helpers
# ------------------------------------------------------------
def _zbar_decode_roi(gray_roi: np.ndarray):
    if not (_ZBAR_OK and enable_zbar and zbar_decode):
        return []
    try:
        return zbar_decode(gray_roi) or []
    except Exception:
        return []

def _zbar_poly_to_quad(poly_pts):
    """
    pyzbar polygon may have >4 points; convert to 4-corner box.
    """
    try:
        arr = np.array([(p.x, p.y) for p in poly_pts], dtype=np.float32)
        if arr.shape[0] < 4:
            return None
        rect = cv2.minAreaRect(arr)
        box = cv2.boxPoints(rect)
        return np.array(box, dtype=np.float32)
    except Exception:
        return None

# ------------------------------------------------------------
# OpenCV QRCodeDetector (optional; can crash on Alpine/musl in some cases)
# ------------------------------------------------------------
qcd = cv2.QRCodeDetector()

def _is_musl():
    try:
        libc = platform.libc_ver()[0] or ""
        return "musl" in libc.lower()
    except Exception:
        return False

_MUSL = _is_musl()

# Auto-disable OpenCV QR in zone-only mode on musl unless explicitly forced
if _MUSL and restrict_to_zones and (not force_opencv_qr_on_musl):
    if opencv_qr_enabled:
        logger.warning("musl detected + restrict_to_zones=true -> disabling OpenCV QRCodeDetector to avoid crashes. (set force_opencv_qr_on_musl=true to override)")
    opencv_qr_enabled = False

# ------------------------------------------------------------
# Zone scanning
# ------------------------------------------------------------
def _scaled_versions(gray_roi: np.ndarray, scale: float):
    if scale <= 1.000001:
        return [gray_roi]

    h, w = gray_roi.shape[:2]
    nh = int(round(h * scale))
    nw = int(round(w * scale))
    m = max(nh, nw)
    if m > zone_max_scaled_dim:
        # clamp scale to max dimension
        scale = float(zone_max_scaled_dim) / float(m)
        nh = int(round(h * scale))
        nw = int(round(w * scale))
        nh = max(1, nh)
        nw = max(1, nw)

    # both cubic and nearest
    return [
        cv2.resize(gray_roi, (nw, nh), interpolation=cv2.INTER_CUBIC),
        cv2.resize(gray_roi, (nw, nh), interpolation=cv2.INTER_NEAREST),
    ]

def scan_zone(frame_gray: np.ndarray, zname: str, box, pad_px: int, scales: list[float], cycle_idx: int):
    h, w = frame_gray.shape[:2]
    try:
        x1, y1, x2, y2 = map(int, box)
    except Exception:
        return None, None  # decoded_det, candidate_det

    x1p = max(0, x1 - pad_px)
    y1p = max(0, y1 - pad_px)
    x2p = min(w - 1, x2 + pad_px)
    y2p = min(h - 1, y2 + pad_px)
    if x2p <= x1p or y2p <= y1p:
        return None, None

    zone_w = max(1, x2 - x1)
    zone_h = max(1, y2 - y1)

    roi = frame_gray[y1p:y2p, x1p:x2p]
    if roi.size == 0:
        return None, None

    # --- ZBar-first strategy (safe on Alpine) ---
    best_decoded = None

    for sc in scales:
        for roi_s in _scaled_versions(roi, sc):
            rh, rw = roi_s.shape[:2]
            for v in _preprocess_gray_variants(roi_s):
                res = _zbar_decode_roi(v)
                if not res:
                    continue

                # pick the largest symbol (zone should contain one)
                best = None
                best_area = -1.0
                best_quad = None
                best_payload = None

                for r in res:
                    payload = ""
                    try:
                        payload = r.data.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        payload = ""
                    if not payload:
                        continue
                    quad = _zbar_poly_to_quad(r.polygon)
                    if quad is None:
                        continue
                    area = float(abs(cv2.contourArea(quad.astype(np.float32))))
                    if area > best_area:
                        best_area = area
                        best_quad = quad
                        best_payload = payload

                if best_quad is None or not best_payload:
                    continue

                # map quad back to full-frame coords
                quad = best_quad.astype(np.float32)
                quad[:, 0] = np.clip(quad[:, 0], 0, rw - 1)
                quad[:, 1] = np.clip(quad[:, 1], 0, rh - 1)
                # reverse scale mapping and offset
                pts_full = (quad / float(sc)) + np.array([x1p, y1p], dtype=np.float32)

                cx = int(np.mean(pts_full[:, 0]))
                cy = int(np.mean(pts_full[:, 1]))

                # compute certainty from ROI quality + size
                edge_px = _edge_px_from_quad(pts_full)
                lap = _laplacian_var(roi)
                con = _contrast_std(roi)
                score = _certainty_score(edge_px, lap, con)

                det = {
                    "payload": best_payload,
                    "points": pts_full.tolist(),
                    "centroid": [cx, cy],
                    "zone": zname,
                    "score": score,
                    "diag": {"edge_px": edge_px, "lap_var": lap, "contrast": con},
                    "decoded": True,
                }

                best_decoded = det
                # early stop if strong
                if score >= zone_early_stop_score:
                    return best_decoded, None
                return best_decoded, None  # decoded is enough

    # If ZBar didn't decode, optionally run OpenCV QR (can crash on musl; auto-disabled)
    candidate = None
    if opencv_qr_enabled:
        # very conservative: only try detectAndDecode on a few variants to reduce crash risk
        try:
            for sc in scales:
                for roi_s in _scaled_versions(roi, sc):
                    rh, rw = roi_s.shape[:2]
                    for v in _preprocess_gray_variants(roi_s):
                        payload, pts = qcd.detectAndDecode(v)
                        payload = (payload or "").strip()
                        if pts is None:
                            continue
                        pts = np.array(pts, dtype=np.float32).reshape(-1, 2)
                        if pts.shape[0] != 4:
                            continue
                        pts[:, 0] = np.clip(pts[:, 0], 0, rw - 1)
                        pts[:, 1] = np.clip(pts[:, 1], 0, rh - 1)
                        pts_full = (pts / float(sc)) + np.array([x1p, y1p], dtype=np.float32)

                        cx = int(np.mean(pts_full[:, 0]))
                        cy = int(np.mean(pts_full[:, 1]))
                        edge_px = _edge_px_from_quad(pts_full)
                        lap = _laplacian_var(roi)
                        con = _contrast_std(roi)
                        bright, dark = _clip_fractions(roi)
                        score = _certainty_score(edge_px, lap, con)

                        if payload:
                            return {
                                "payload": payload,
                                "points": pts_full.tolist(),
                                "centroid": [cx, cy],
                                "zone": zname,
                                "score": score,
                                "diag": {"edge_px": edge_px, "lap_var": lap, "contrast": con},
                                "decoded": True,
                            }, None

                        # no payload but points => candidate
                        reason = _failure_reason_no_points(zone_w, zone_h, lap, con, bright, dark)
                        cand = {
                            "payload": None,
                            "points": pts_full.tolist(),
                            "centroid": [cx, cy],
                            "zone": zname,
                            "score": score,
                            "reason": reason,
                            "diag": {"edge_px": edge_px, "lap_var": lap, "contrast": con},
                            "decoded": False,
                        }
                        if candidate is None or float(cand.get("score") or 0.0) > float(candidate.get("score") or 0.0):
                            candidate = cand
        except Exception:
            # Python-level exception; C++ abort cannot be caught
            pass

    # Build a "no candidate" reason based on ROI quality
    lap = _laplacian_var(roi)
    con = _contrast_std(roi)
    bright, dark = _clip_fractions(roi)
    reason = _failure_reason_no_points(zone_w, zone_h, lap, con, bright, dark)

    if candidate is not None and overlay_show_candidates:
        return None, candidate

    # return none; zone overlay will show MISS + reason (from roi stats)
    return None, {
        "payload": None,
        "points": None,  # no quad to draw
        "centroid": [int((x1 + x2) / 2), int((y1 + y2) / 2)],
        "zone": zname,
        "score": _certainty_score(min(zone_w, zone_h), lap, con),
        "reason": reason,
        "diag": {"edge_px": float(min(zone_w, zone_h)), "lap_var": lap, "contrast": con},
        "decoded": False,
        "no_quad": True,
    }

# ------------------------------------------------------------
# Detection entrypoint (zones-only)
# ------------------------------------------------------------
def detect_qr(frame_bgr: np.ndarray, zones_dict: dict, cycle_idx: int):
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    zones_ok = isinstance(zones_dict, dict) and bool(zones_dict)

    detections = []

    if restrict_to_zones and not zones_ok:
        # safety fallback: nothing to scan
        return [], True

    if zones_ok:
        pad = max(0, int(roi_padding_px))
        scales = _dedupe_sorted(ROI_SCALES + ZONE_EXTRA_SCALES)
        if not scales:
            scales = [2.0, 3.0, 4.0, 6.0, 8.0, 10.0]

        for zname, box in zones_dict.items():
            dec, cand = scan_zone(frame_gray, str(zname), box, pad, scales, cycle_idx)
            if dec is not None:
                detections.append(dec)
            elif cand is not None:
                # always append: used for zone status even if no quad
                detections.append(cand)

        return detections, True if restrict_to_zones else False

    return [], False

# ------------------------------------------------------------
# Zone status computation
# ------------------------------------------------------------
def compute_zone_status(zones_dict: dict, detections: list):
    status = {}
    if not isinstance(zones_dict, dict):
        return status

    for zname in zones_dict.keys():
        status[zname] = {"kind": "none", "det": None}

    for d in detections:
        z = d.get("zone")
        if not z or z not in status:
            continue
        if d.get("decoded", False):
            status[z] = {"kind": "decoded", "det": d}
        else:
            # keep best "candidate/no-quad" by score
            cur = status[z]
            if cur["kind"] != "decoded":
                if cur["det"] is None or float(d.get("score") or 0.0) > float(cur["det"].get("score") or 0.0):
                    status[z] = {"kind": "candidate", "det": d}

    return status

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

def _edge_px_from_det(det):
    diag = det.get("diag") or {}
    edge = diag.get("edge_px", None)
    try:
        return int(round(float(edge))) if edge is not None else None
    except Exception:
        return None

def draw_overlay(frame, detections, zones_dict):
    out = frame.copy()
    h, w = out.shape[:2]

    ORANGE = (0, 165, 255)
    RED = (0, 0, 255)
    CAND = (255, 255, 0)
    OKBG = (0, 170, 0)
    MISSBG = (80, 80, 80)
    CAND_BG = (255, 255, 0)

    zone_status = compute_zone_status(zones_dict, detections) if overlay_show_zone_status else {}

    # Zones + status
    if isinstance(zones_dict, dict) and zones_dict:
        font = cv2.FONT_HERSHEY_SIMPLEX
        pad = 3
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

            zl = _safe_label(str(zname), 32)
            (tw, th), base = cv2.getTextSize(zl, font, 0.55, 2)
            zbx2 = min(w - 1, x1 + tw + pad * 2)
            zby2 = min(h - 1, y1 + th + base + pad * 2)
            cv2.rectangle(out, (x1, y1), (zbx2, zby2), ORANGE, -1)
            cv2.putText(out, zl, (x1 + pad, min(h - 1, y1 + th + pad)), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

            st = zone_status.get(zname, {"kind": "none", "det": None})
            kind = st.get("kind", "none")
            det = st.get("det")

            if kind == "decoded" and det:
                parts = ["OK"]
                if overlay_show_scores:
                    p = _pct(det.get("score"))
                    if p is not None:
                        parts.append(f"{p}%")
                if overlay_show_size_px:
                    ep = _edge_px_from_det(det)
                    if ep is not None:
                        parts.append(f"{ep}px")
                text = " ".join(parts)
                bg = OKBG
                fg = (255, 255, 255)
            elif kind == "candidate" and det:
                parts = ["MISS"]
                if overlay_show_scores:
                    p = _pct(det.get("score"))
                    if p is not None:
                        parts.append(f"{p}%")
                if overlay_show_size_px:
                    ep = _edge_px_from_det(det)
                    if ep is not None:
                        parts.append(f"{ep}px")
                if overlay_show_candidate_reason:
                    r = det.get("reason") or "unknown"
                    parts.append(r)
                text = " ".join(parts)
                bg = CAND_BG
                fg = (0, 0, 0)
            else:
                text = "MISS no_candidate"
                bg = MISSBG
                fg = (255, 255, 255)

            text = _safe_label(text, 48)
            (stw, sth), sbase = cv2.getTextSize(text, font, 0.5, 1)
            sy1 = min(h - 1, zby2 + 2)
            sy2 = min(h - 1, sy1 + sth + sbase + pad * 2)
            sx2 = min(w - 1, x1 + stw + pad * 2)
            if sy2 > sy1:
                cv2.rectangle(out, (x1, sy1), (sx2, sy2), bg, -1)
                cv2.putText(out, text, (x1 + pad, min(h - 1, sy1 + sth + pad)), font, 0.5, fg, 1, cv2.LINE_AA)

    # Draw decoded quads (and candidates that have a quad)
    for d in detections:
        pts_list = d.get("points")
        if not pts_list or d.get("no_quad"):
            continue
        pts = np.array(pts_list, dtype=np.int32).reshape(-1, 1, 2)
        if pts.shape[0] != 4:
            continue
        color = RED if d.get("decoded") else CAND
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=2)

        payload = d.get("payload") or ("CAND" if not d.get("decoded") else "")
        label = payload
        if overlay_show_scores:
            p = _pct(d.get("score"))
            if p is not None:
                label = f"{label} {p}%"
        if overlay_show_size_px:
            ep = _edge_px_from_det(d)
            if ep is not None:
                label = f"{label} {ep}px"
        if (not d.get("decoded")) and overlay_show_candidate_reason:
            r = d.get("reason")
            if r:
                label = f"{label} {r}"

        label = _safe_label(label)
        if label:
            x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
            font = cv2.FONT_HERSHEY_SIMPLEX
            (tw, th), base = cv2.getTextSize(label, font, 0.6, 2)
            pad = 4
            x1 = max(0, x)
            y1 = max(0, y - th - base - pad * 2)
            x2 = min(out.shape[1] - 1, x + tw + pad * 2)
            y2 = min(out.shape[0] - 1, y)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, -1)
            cv2.putText(out, label, (x + pad, max(0, y - pad)), font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

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
  <p>Last frame info: {fi}</p>
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
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), OverlayHandler)
    logger.info("Overlay HTTP server listening on :%d (/%s)", HTTP_PORT, OVERLAY_PNG_NAME)
    httpd.serve_forever()

threading.Thread(target=start_http_server, daemon=True).start()

# ------------------------------------------------------------
# Main loop
# ------------------------------------------------------------
logger.info(
    "Starting QR Inventory (mode=%s interval=%ss required=%s tls_verify=%s overlay_png_name=%s "
    "restrict_to_zones=%s musl=%s enable_zbar=%s zbar_ok=%s opencv_qr_enabled=%s force_opencv_qr_on_musl=%s "
    "roi_scales=%s zone_extra_scales=%s zone_max_scaled_dim=%s)",
    camera_mode, interval, required, tls_verify, OVERLAY_PNG_NAME,
    restrict_to_zones, _MUSL, enable_zbar, _ZBAR_OK, opencv_qr_enabled, force_opencv_qr_on_musl,
    ROI_SCALES, ZONE_EXTRA_SCALES, zone_max_scaled_dim
)

_log_stream_info("STARTUP")
cycle_idx = 0
last_stream_info_ts = 0.0

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

        if stream_info_interval_minutes > 0:
            now = time.time()
            if last_stream_info_ts == 0 or (now - last_stream_info_ts) >= stream_info_interval_minutes * 60:
                _log_stream_info("PERIODIC")
                last_stream_info_ts = now

        frame = get_frame_ffmpeg(stream_url)
        if frame is None:
            time.sleep(interval)
            continue

        detections, zone_only_active = detect_qr(frame, zones, cycle_idx=cycle_idx)

        # decoded only -> inventory mapping
        for det in detections:
            if not det.get("decoded", False):
                continue
            payload = det.get("payload")
            zone = det.get("zone")
            if not payload or not zone:
                continue
            history[payload].append(zone)
            logger.info("Detected payload=%s zone=%s history=%s", payload, zone, list(history[payload]))
            if len(history[payload]) >= history_maxlen and len(set(history[payload])) == 1:
                persist_mapping(payload, zone)

        overlay = draw_overlay(frame, detections, zones)
        frame_png = _encode_png(frame)
        overlay_png = _encode_png(overlay)

        fi = {
            "frame_w": int(frame.shape[1]),
            "frame_h": int(frame.shape[0]),
            "decoded": sum(1 for d in detections if d.get("decoded", False)),
            "candidates": sum(1 for d in detections if not d.get("decoded", False)),
            "restrict_to_zones_active": zone_only_active,
        }

        with STATE_LOCK:
            STATE["ts"] = int(time.time())
            STATE["frame_png"] = frame_png
            STATE["overlay_png"] = overlay_png
            STATE["detections"] = detections
            STATE["last_frame_info"] = fi

    except Exception as e:
        logger.exception("Error in main loop: %s", e)

    time.sleep(interval)