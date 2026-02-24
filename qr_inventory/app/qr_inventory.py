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
    return bool(opts.get(key, default))

def _opt_str(key, default):
    v = opts.get(key, default)
    return default if v is None else str(v)

interval = _opt_int('interval_seconds', 60)
required = _opt_int('required_consistency', 3)
camera_mode = _opt_str('camera_mode', 'rtsps').lower()
stream_url = opts.get('rtsp_url')
tls_verify = _opt_bool('tls_verify', False)

# ------------------------------------------------------------
# Restrict detection/decoding to zones only (toggle)
# ------------------------------------------------------------
restrict_to_zones = _opt_bool("restrict_to_zones", False)

# ------------------------------------------------------------
# Accuracy / small QR tuning
# ------------------------------------------------------------
zone_fallback = _opt_bool("zone_fallback", True)
use_preprocess = _opt_bool("use_preprocess", True)
roi_padding_px = _opt_int("roi_padding_px", 60)

roi_scale = _opt_float("roi_scale", 2.0)  # backward compat
roi_scales_raw = opts.get("roi_scales", None)
warp_scale = _opt_float("warp_scale", 5.0)
try_invert = _opt_bool("try_invert", True)
max_candidates = _opt_int("max_candidates", 160)

# Deeper zone scanning
zone_deep_scan = _opt_bool("zone_deep_scan", True)
zone_extra_scales_raw = opts.get("zone_extra_scales", "6.0,8.0")
zone_early_stop_score = _opt_float("zone_early_stop_score", 0.85)

# NEW: decode using found points (big win for your “MISS no_candidate” zones)
zone_point_decode = _opt_bool("zone_point_decode", True)

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

def _parse_roi_scales(raw, fallback):
    base = _parse_float_list(raw, [fallback if fallback and fallback > 0 else 2.0])
    seen = set()
    out = []
    for s in base:
        k = round(float(s), 6)
        if k not in seen:
            seen.add(k)
            out.append(float(s))
    return out

ROI_SCALES = _parse_roi_scales(roi_scales_raw, roi_scale)
ZONE_EXTRA_SCALES = _parse_float_list(zone_extra_scales_raw, [6.0, 8.0])

# ------------------------------------------------------------
# Overlay options
# ------------------------------------------------------------
overlay_show_candidates = _opt_bool("overlay_show_candidates", True)
overlay_max_candidates = max(0, _opt_int("overlay_max_candidates", 40))
overlay_show_scores = _opt_bool("overlay_show_scores", True)
overlay_show_size_px = _opt_bool("overlay_show_size_px", True)
overlay_show_candidate_reason = _opt_bool("overlay_show_candidate_reason", True)
overlay_show_zone_status = _opt_bool("overlay_show_zone_status", True)

# ------------------------------------------------------------
# Diagnostics logging (no image saving)
# ------------------------------------------------------------
debug_metrics = _opt_bool("debug_metrics", False)
debug_log_every = max(1, _opt_int("debug_log_every", 1))
debug_max_failed_logs = max(0, _opt_int("debug_max_failed_logs", 20))
stream_info_interval_minutes = max(0, _opt_int("stream_info_interval_minutes", 0))  # 0=only startup

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
    for name, box in zones_dict.items():
        try:
            x1, y1, x2, y2 = box
        except Exception:
            continue
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return name
    return None

history_maxlen = required if required and required > 0 else 1
history = defaultdict(lambda: deque(maxlen=history_maxlen))
confirmed = {}

qcd = cv2.QRCodeDetector()

# ------------------------------------------------------------
# Stream info (ffprobe)
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
# Detection helpers + scoring
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

def _clip_fractions(gray: np.ndarray):
    try:
        bright = float(np.mean(gray >= 250))
        dark = float(np.mean(gray <= 5))
        return bright, dark
    except Exception:
        return 0.0, 0.0

def _certainty_score(edge_px: float, lap_var: float, contrast: float) -> float:
    s_size = min(1.0, edge_px / 90.0)
    s_sharp = min(1.0, lap_var / 180.0)
    s_con = min(1.0, contrast / 45.0)
    score = 0.45 * s_size + 0.40 * s_sharp + 0.15 * s_con
    return float(max(0.0, min(1.0, score)))

def _failure_reason(edge_px: float, lap_var: float, contrast: float, bright_clip: float, dark_clip: float) -> str:
    if bright_clip >= 0.35:
        return "reflection"
    if edge_px < 35:
        return "too_small"
    if lap_var < 40:
        return "blurry"
    if contrast < 18:
        return "low_contrast"
    if dark_clip >= 0.35:
        return "shadow"
    return "unknown"

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

def _warp_patch(frame_gray: np.ndarray, pts_full: np.ndarray):
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

def _analyze_warp(warp: np.ndarray, edge: float, warp_size: int):
    lap = _laplacian_var(warp)
    con = _contrast_std(warp)
    bright, dark = _clip_fractions(warp)
    score = _certainty_score(edge, lap, con)
    reason = _failure_reason(edge, lap, con, bright, dark)
    diag = {
        "edge_px": edge,
        "lap_var": lap,
        "contrast": con,
        "warp_size": warp_size,
        "bright_clip": bright,
        "dark_clip": dark,
    }
    return score, reason, diag

def _decode_warp_variants(warp: np.ndarray) -> str:
    for v in _preprocess_gray_variants(warp):
        try:
            payload, _, _ = qcd.detectAndDecode(v)
        except Exception:
            payload = ""
        payload = (payload or "").strip()
        if payload:
            return payload
    return ""

def _try_decode_with_points(images: list, pts4: np.ndarray) -> str:
    """
    Retry decoding with known points. This is the key change that fixes many
    "MISS no_candidate" zones: detect returned points but decode failed.
    """
    if pts4 is None:
        return ""
    pts4 = np.array(pts4, dtype=np.float32).reshape(-1, 2)
    if pts4.shape[0] != 4:
        return ""

    for img in images:
        try:
            val, _ = qcd.decode(img, pts4)
        except Exception:
            val = ""
        val = (val or "").strip()
        if val:
            return val

        try:
            val2, _ = qcd.decodeCurved(img, pts4)
        except Exception:
            val2 = ""
        val2 = (val2 or "").strip()
        if val2:
            return val2

    return ""

def _pct(score):
    try:
        if score is None:
            return None
        return int(round(float(score) * 100))
    except Exception:
        return None

# ------------------------------------------------------------
# Zone-optimized scan (improved)
# ------------------------------------------------------------
def scan_zone(frame_gray: np.ndarray, zname: str, box, pad_px: int, scales: list[float], cycle_idx: int):
    h, w = frame_gray.shape[:2]
    try:
        x1, y1, x2, y2 = map(int, box)
    except Exception:
        return None, None

    x1p = max(0, x1 - pad_px)
    y1p = max(0, y1 - pad_px)
    x2p = min(w - 1, x2 + pad_px)
    y2p = min(h - 1, y2 + pad_px)
    if x2p <= x1p or y2p <= y1p:
        return None, None

    roi = frame_gray[y1p:y2p, x1p:x2p]
    if roi.size == 0:
        return None, None

    best_decoded = None
    best_candidate = None

    def map_pts(pts_local, sc):
        pts_local = np.array(pts_local, dtype=np.float32).reshape(-1, 2)
        if pts_local.shape[0] != 4:
            return None
        return (pts_local / sc) + np.array([x1p, y1p], dtype=np.float32)

    def consider_candidate(pts_full):
        nonlocal best_candidate
        warp, ws, edge = _warp_patch(frame_gray, pts_full)
        if warp is None:
            return
        score, reason, diag = _analyze_warp(warp, edge, ws)
        cand = {
            "payload": None,
            "points": pts_full.tolist(),
            "centroid": [int(np.mean(pts_full[:, 0])), int(np.mean(pts_full[:, 1]))],
            "zone": zname,
            "score": score,
            "reason": reason,
            "diag": diag,
            "decoded": False,
        }
        if best_candidate is None or float(score) > float(best_candidate.get("score") or 0.0):
            best_candidate = cand

    def consider_decoded(payload, pts_full):
        nonlocal best_decoded
        warp, ws, edge = _warp_patch(frame_gray, pts_full)
        if warp is None:
            score = None
            diag = None
        else:
            score, _, diag = _analyze_warp(warp, edge, ws)

        det = {
            "payload": payload,
            "points": pts_full.tolist(),
            "centroid": [int(np.mean(pts_full[:, 0])), int(np.mean(pts_full[:, 1]))],
            "zone": zname,
            "score": score,
            "diag": diag,
            "decoded": True,
        }
        if best_decoded is None:
            best_decoded = det
        else:
            s_new = float(score) if score is not None else 0.0
            s_old = float(best_decoded.get("score") or 0.0)
            if s_new > s_old:
                best_decoded = det

    # Try both interpolations when scaling > 1 (NEAREST often helps QR)
    interp_modes = [("cubic", cv2.INTER_CUBIC), ("nearest", cv2.INTER_NEAREST)]

    for sc in scales:
        sc = float(sc) if sc else 1.0
        if sc <= 0:
            sc = 1.0

        roi_scaled_variants = []
        if sc == 1.0:
            roi_scaled_variants.append(("orig", roi))
        else:
            for tag, interp in interp_modes:
                roi_s = cv2.resize(roi, None, fx=sc, fy=sc, interpolation=interp)
                roi_scaled_variants.append((tag, roi_s))

        for _, roi_s in roi_scaled_variants:
            # Keep a base image list for point-based decode retries
            base_imgs = [roi_s]
            if try_invert:
                base_imgs.append(255 - roi_s)

            for variant in _preprocess_gray_variants(roi_s):
                # 1) detectAndDecode (single) - but if payload empty and points exist, keep points
                try:
                    payload, pts = qcd.detectAndDecode(variant)
                except Exception:
                    payload, pts = "", None
                payload = (payload or "").strip()

                if pts is not None:
                    pts_full = map_pts(pts, sc)
                    if pts_full is not None:
                        if payload:
                            consider_decoded(payload, pts_full)
                        else:
                            # NEW: retry decode using known points
                            if zone_point_decode:
                                pts4 = np.array(pts, dtype=np.float32).reshape(-1, 2)
                                decoded_retry = _try_decode_with_points(base_imgs + [variant], pts4)
                                if decoded_retry:
                                    consider_decoded(decoded_retry, pts_full)
                                else:
                                    consider_candidate(pts_full)
                            else:
                                consider_candidate(pts_full)

                        if best_decoded and (best_decoded.get("score") is not None) and float(best_decoded["score"]) >= zone_early_stop_score:
                            return best_decoded, best_candidate

                # 2) detectAndDecodeCurved
                if zone_deep_scan:
                    try:
                        payload2, pts2 = qcd.detectAndDecodeCurved(variant)
                    except Exception:
                        payload2, pts2 = "", None
                    payload2 = (payload2 or "").strip()

                    if pts2 is not None:
                        pts_full2 = map_pts(pts2, sc)
                        if pts_full2 is not None:
                            if payload2:
                                consider_decoded(payload2, pts_full2)
                            else:
                                if zone_point_decode:
                                    pts4 = np.array(pts2, dtype=np.float32).reshape(-1, 2)
                                    decoded_retry = _try_decode_with_points(base_imgs + [variant], pts4)
                                    if decoded_retry:
                                        consider_decoded(decoded_retry, pts_full2)
                                    else:
                                        consider_candidate(pts_full2)
                                else:
                                    consider_candidate(pts_full2)

                            if best_decoded and (best_decoded.get("score") is not None) and float(best_decoded["score"]) >= zone_early_stop_score:
                                return best_decoded, best_candidate

                # 3) Multi decode (sometimes catches cases single misses)
                decoded_list, cand_list = _detect_and_decode_multi(variant)
                for p, pts_m in decoded_list:
                    pts_full = map_pts(pts_m, sc)
                    if pts_full is not None:
                        consider_decoded(p, pts_full)
                for pts_m in cand_list:
                    pts_full = map_pts(pts_m, sc)
                    if pts_full is not None:
                        # NEW: retry decode with points for multi candidates too
                        if zone_point_decode:
                            pts4 = np.array(pts_m, dtype=np.float32).reshape(-1, 2)
                            decoded_retry = _try_decode_with_points(base_imgs + [variant], pts4)
                            if decoded_retry:
                                consider_decoded(decoded_retry, pts_full)
                            else:
                                consider_candidate(pts_full)
                        else:
                            consider_candidate(pts_full)

                # 4) detectMulti points + decode using points, then warp fallback
                for pts_m in _detect_points(variant):
                    pts_full = map_pts(pts_m, sc)
                    if pts_full is None:
                        continue

                    # NEW: decode directly with points
                    decoded_retry = ""
                    if zone_point_decode:
                        pts4 = np.array(pts_m, dtype=np.float32).reshape(-1, 2)
                        decoded_retry = _try_decode_with_points(base_imgs + [variant], pts4)

                    if decoded_retry:
                        consider_decoded(decoded_retry, pts_full)
                    else:
                        # warp-based retry
                        warp, ws, edge = _warp_patch(frame_gray, pts_full)
                        if warp is not None:
                            payload_w = _decode_warp_variants(warp)
                            if payload_w:
                                consider_decoded(payload_w, pts_full)
                            else:
                                consider_candidate(pts_full)
                        else:
                            consider_candidate(pts_full)

                    if best_decoded and (best_decoded.get("score") is not None) and float(best_decoded["score"]) >= zone_early_stop_score:
                        return best_decoded, best_candidate

            if best_decoded and best_decoded.get("score") is not None and float(best_decoded["score"]) >= zone_early_stop_score:
                break

        if best_decoded and best_decoded.get("score") is not None and float(best_decoded["score"]) >= zone_early_stop_score:
            break

    return best_decoded, best_candidate

# ------------------------------------------------------------
# Detection entrypoint
# ------------------------------------------------------------
def detect_qr(frame_bgr: np.ndarray, zones_dict: dict, cycle_idx: int):
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    zones_ok = isinstance(zones_dict, dict) and bool(zones_dict)
    zone_restrict_active = bool(restrict_to_zones and zones_ok)

    detections = []

    if zone_restrict_active:
        pad = max(0, int(roi_padding_px))

        eff = ROI_SCALES[:]
        if zone_deep_scan:
            eff = eff + ZONE_EXTRA_SCALES
        eff = sorted({round(float(s), 6) for s in eff if float(s) > 0})
        eff = [float(s) for s in eff] or [2.0, 3.0, 4.0, 6.0, 8.0]

        for zname, box in zones_dict.items():
            dec, cand = scan_zone(frame_gray, str(zname), box, pad, eff, cycle_idx)
            if dec is not None:
                detections.append(dec)
            elif cand is not None and overlay_show_candidates:
                detections.append(cand)

        return detections, True

    # Non-restricted mode: keep earlier behavior (not changed here)
    # If you want, we can apply the same point-based decode there too.
    best = {}
    cand = {}

    def add_decoded(payload, pts_full):
        warp, ws, edge = _warp_patch(frame_gray, pts_full)
        if warp is None:
            score = None
            diag = None
        else:
            score, _, diag = _analyze_warp(warp, edge, ws)

        cx = int(np.mean(pts_full[:, 0])); cy = int(np.mean(pts_full[:, 1]))
        zone = centroid_to_zone(cx, cy, zones_dict)
        det = {
            "payload": payload,
            "points": pts_full.tolist(),
            "centroid": [cx, cy],
            "zone": zone,
            "score": score,
            "diag": diag,
            "decoded": True,
        }
        prev = best.get(payload)
        if prev is None:
            best[payload] = det
        else:
            s_new = float(score) if score is not None else 0.0
            s_old = float(prev.get("score") or 0.0)
            if s_new > s_old:
                best[payload] = det

    def add_candidate(pts_full):
        warp, ws, edge = _warp_patch(frame_gray, pts_full)
        if warp is None:
            return
        score, reason, diag = _analyze_warp(warp, edge, ws)
        cx = int(np.mean(pts_full[:, 0])); cy = int(np.mean(pts_full[:, 1]))
        bucket = (int(cx // 20), int(cy // 20))
        zone = centroid_to_zone(cx, cy, zones_dict)
        det = {
            "payload": None,
            "points": pts_full.tolist(),
            "centroid": [cx, cy],
            "zone": zone,
            "score": score,
            "reason": reason,
            "diag": diag,
            "decoded": False,
        }
        prev = cand.get(bucket)
        if prev is None or float(score) > float(prev.get("score") or 0.0):
            cand[bucket] = det

    for variant in _preprocess_gray_variants(frame_gray):
        decoded_list, cand_list = _detect_and_decode_multi(variant)
        for p, pts in decoded_list:
            pts_full = np.array(pts, dtype=np.float32).reshape(-1, 2)
            if pts_full.shape[0] == 4:
                add_decoded(p, pts_full)
        for pts in cand_list:
            pts_full = np.array(pts, dtype=np.float32).reshape(-1, 2)
            if pts_full.shape[0] == 4:
                add_candidate(pts_full)
        for pts in _detect_points(variant):
            pts_full = np.array(pts, dtype=np.float32).reshape(-1, 2)
            if pts_full.shape[0] == 4:
                add_candidate(pts_full)

    detections.extend(best.values())
    if overlay_show_candidates:
        candidates = sorted(cand.values(), key=lambda d: float(d.get("score") or 0.0), reverse=True)
        detections.extend(candidates[:overlay_max_candidates] if overlay_max_candidates > 0 else candidates)

    return detections, False

# ------------------------------------------------------------
# Zone status computation
# ------------------------------------------------------------
def compute_zone_status(zones_dict: dict, detections: list):
    status = {}
    if not isinstance(zones_dict, dict):
        return status

    def det_key(d):
        try:
            score = float(d.get("score") or 0.0)
        except Exception:
            score = 0.0
        diag = d.get("diag") or {}
        try:
            edge = float(diag.get("edge_px") or 0.0)
        except Exception:
            edge = 0.0
        return (score, edge)

    for zname in zones_dict.keys():
        status[zname] = {"kind": "none", "det": None}

    for d in detections:
        z = d.get("zone")
        if not z or z not in status:
            continue
        if d.get("decoded", True):
            cur = status[z]
            if cur["kind"] != "decoded" or det_key(d) > det_key(cur["det"]):
                status[z] = {"kind": "decoded", "det": d}
        else:
            cur = status[z]
            if cur["kind"] == "decoded":
                continue
            if cur["kind"] != "candidate" or det_key(d) > det_key(cur["det"]):
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
    if edge is None:
        try:
            pts4 = np.array(det.get("points", []), dtype=np.float32).reshape(-1, 2)
            if pts4.shape[0] == 4:
                edge = _max_edge_len(pts4)
        except Exception:
            edge = None
    try:
        return int(round(float(edge))) if edge is not None else None
    except Exception:
        return None

def draw_overlay(frame, detections, zones_dict):
    out = frame.copy()
    h, w = out.shape[:2]

    ORANGE = (0, 165, 255)
    RED = (0, 0, 255)
    CAND = (255, 255, 0)      # cyan
    OKBG = (0, 170, 0)        # green
    MISSBG = (80, 80, 80)     # gray
    CAND_BG = (255, 255, 0)   # cyan

    zone_status = compute_zone_status(zones_dict, detections) if overlay_show_zone_status else {}

    # Zones first + status
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

            if overlay_show_zone_status:
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

                text = _safe_label(text, 44)
                (stw, sth), sbase = cv2.getTextSize(text, font, 0.5, 1)
                sy1 = min(h - 1, zby2 + 2)
                sy2 = min(h - 1, sy1 + sth + sbase + pad * 2)
                sx2 = min(w - 1, x1 + stw + pad * 2)
                if sy2 > sy1:
                    cv2.rectangle(out, (x1, sy1), (sx2, sy2), bg, -1)
                    cv2.putText(out, text, (x1 + pad, min(h - 1, sy1 + sth + pad)), font, 0.5, fg, 1, cv2.LINE_AA)

    decoded = [d for d in detections if d.get("decoded", True)]
    candidates = [d for d in detections if not d.get("decoded", True)]

    def draw_det(det, color, prefix="CAND"):
        pts_list = det.get("points", [])
        if not pts_list:
            return
        pts = np.array(pts_list, dtype=np.int32).reshape((-1, 1, 2))
        if pts.size == 0:
            return
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=2)

        payload = det.get("payload", "")
        label = payload if payload else prefix

        if overlay_show_scores:
            p = _pct(det.get("score"))
            if p is not None:
                label = f"{label} {p}%"
        if overlay_show_size_px:
            ep = _edge_px_from_det(det)
            if ep is not None:
                label = f"{label} {ep}px"
        if (not det.get("decoded", True)) and overlay_show_candidate_reason:
            r = det.get("reason")
            if r:
                label = f"{label} {r}"

        label = _safe_label(label)
        if not label:
            return

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

    for d in candidates:
        draw_det(d, CAND, "CAND")
    for d in decoded:
        draw_det(d, RED, "QR")

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
    "restrict_to_zones=%s zone_deep_scan=%s zone_point_decode=%s ROI_SCALES=%s zone_extra_scales=%s "
    "zone_early_stop_score=%.2f warp_scale=%s try_invert=%s adaptive_block_sizes=%s)",
    camera_mode, interval, required, tls_verify, OVERLAY_PNG_NAME,
    restrict_to_zones, zone_deep_scan, zone_point_decode, ROI_SCALES, ZONE_EXTRA_SCALES,
    zone_early_stop_score, warp_scale, try_invert, adaptive_block_sizes,
)

_log_stream_info("STARTUP")
cycle_idx = 0
last_stream_info_ts = 0.0
warned_no_zones = False

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

        if restrict_to_zones and (not isinstance(zones, dict) or not zones):
            if not warned_no_zones:
                logger.warning("restrict_to_zones=true but zones is empty/invalid. Falling back to full-frame detection.")
                warned_no_zones = True

        if stream_info_interval_minutes > 0:
            now = time.time()
            if last_stream_info_ts == 0 or (now - last_stream_info_ts) >= stream_info_interval_minutes * 60:
                _log_stream_info("PERIODIC")
                last_stream_info_ts = now

        frame = get_frame_ffmpeg(stream_url)
        if frame is None:
            time.sleep(interval)
            continue

        fh, fw = frame.shape[:2]
        detections, zone_restrict_active = detect_qr(frame, zones, cycle_idx=cycle_idx)

        # decoded only -> inventory mapping
        for det in detections:
            if not det.get("decoded", True):
                continue

            info = det["payload"]
            cx, cy = det["centroid"]
            zone = det["zone"]

            if zone_restrict_active and zone is None:
                continue

            history[info].append(zone)

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
            "detections_total": len(detections),
            "decoded": sum(1 for d in detections if d.get("decoded", True)),
            "candidates": sum(1 for d in detections if not d.get("decoded", True)),
            "restrict_to_zones_active": zone_restrict_active,
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