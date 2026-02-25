import time
import json
import os
import sys
import base64
import logging
import subprocess
import threading
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import cv2
import numpy as np

# Optional ZBar
try:
    from pyzbar.pyzbar import decode as zbar_decode
    try:
        from pyzbar.pyzbar import ZBarSymbol
        _ZBAR_QR_SYMBOL = ZBarSymbol.QRCODE
    except Exception:
        _ZBAR_QR_SYMBOL = None
    _ZBAR_OK = True
except Exception:
    zbar_decode = None
    _ZBAR_OK = False
    _ZBAR_QR_SYMBOL = None

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

restrict_to_zones = _opt_bool("restrict_to_zones", False)

use_preprocess = _opt_bool("use_preprocess", True)
try_invert = _opt_bool("try_invert", True)
roi_padding_px = _opt_int("roi_padding_px", 60)


enable_cutout = _opt_bool("enable_cutout", True)
cutout_min_area = max(50, _opt_int("cutout_min_area", 500))
cutout_ar_lo = _opt_float("cutout_ar_lo", 0.60)
cutout_ar_hi = _opt_float("cutout_ar_hi", 1.40)
cutout_inner_pad_frac = _opt_float("cutout_inner_pad_frac", 0.00)
cutout_border_frac = _opt_float("cutout_border_frac", 0.20)
cutout_border_min_px = max(0, _opt_int("cutout_border_min_px", 16))
cutout_open_ksize = max(1, _opt_int("cutout_open_ksize", 3))
if cutout_open_ksize % 2 == 0:
    cutout_open_ksize += 1
cutout_open_iter = max(0, _opt_int("cutout_open_iter", 1))

cutout_close_ksize = max(1, _opt_int("cutout_close_ksize", 9))
if cutout_close_ksize % 2 == 0:
    cutout_close_ksize += 1
cutout_close_iter = max(0, _opt_int("cutout_close_iter", 2))
cutout_min_side_px = max(0, _opt_int("cutout_min_side_px", 24))

cutout_safety_pad_px = max(0, _opt_int("cutout_safety_pad_px", 0))
cutout_detect_enable = _opt_bool("cutout_detect_enable", True)
cutout_detect_tighten = _opt_bool("cutout_detect_tighten", True)
cutout_detect_scales_raw = opts.get("cutout_detect_scales", "2.0,3.0,4.0")

# If OpenCV QR localization returns a quad (even when decoding fails), we can improve
# decode reliability by forcing everything *outside* that quad to pure white in the
# decode input. This removes paper texture / background clutter and effectively
# creates a cleaner quiet-zone around the symbol.
cutout_whiten_outside_quad = _opt_bool("cutout_whiten_outside_quad", True)



roi_scale = _opt_float("roi_scale", 2.0)
roi_scales_raw = opts.get("roi_scales", None)
zone_extra_scales_raw = opts.get("zone_extra_scales", "6.0,8.0,10.0")

zone_max_scaled_dim = max(400, _opt_int("zone_max_scaled_dim", 2400))
zone_early_stop_score = _opt_float("zone_early_stop_score", 0.85)

zone_quad_in_zone_min_ratio = _opt_float("zone_quad_in_zone_min_ratio", 0.60)

enable_zbar = _opt_bool("enable_zbar", True)
zbar_qrcode_only = _opt_bool("zbar_qrcode_only", True)

opencv_subprocess_fallback = _opt_bool("opencv_subprocess_fallback", True)
opencv_fallback_timeout_s = _opt_float("opencv_fallback_timeout_s", 3.0)
opencv_fallback_attempts = max(1, _opt_int("opencv_fallback_attempts", 10))

overlay_show_scores = _opt_bool("overlay_show_scores", True)
overlay_show_size_px = _opt_bool("overlay_show_size_px", True)
overlay_show_candidate_reason = _opt_bool("overlay_show_candidate_reason", True)
overlay_show_zone_status = _opt_bool("overlay_show_zone_status", True)
overlay_show_unresolved_quads = _opt_bool("overlay_show_unresolved_quads", True)

overlay_fill_unresolved_gap = _opt_bool("overlay_fill_unresolved_gap", True)
overlay_fill_unresolved_alpha = _opt_float("overlay_fill_unresolved_alpha", 0.22)

stream_info_interval_minutes = max(0, _opt_int("stream_info_interval_minutes", 0))
debug_zone = _opt_str("debug_zone", "").strip()

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
CUTOUT_DETECT_SCALES = _dedupe_sorted(_parse_float_list(cutout_detect_scales_raw, [2.0, 3.0, 4.0]))


# ------------------------------------------------------------
# Overlay PNG name
# ------------------------------------------------------------
_overlay_name = os.path.basename(_opt_str('overlay_png_name', 'overlay.png').strip().lstrip('/')) or "overlay.png"
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += ".png"
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
# Utility helpers (FIX: _safe_label added)
# ------------------------------------------------------------
def _safe_label(s: str, max_len: int = 96) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s

def _encode_png(img):
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes() if ok else None

def _edge_px_from_det(det):
    diag = det.get("diag") or {}
    edge = diag.get("edge_px", None)
    try:
        return int(round(float(edge))) if edge is not None else None
    except Exception:
        return None

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
        return None if b == 0 else round(a / b, 3)
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
    logger.info("%s: Stream info: codec=%s profile=%s pix_fmt=%s size=%sx%s fps=%s bitrate_kbps=%s",
                tag, info.get("codec_name"), info.get("profile"), info.get("pix_fmt"),
                info.get("width"), info.get("height"), fps, br_kbps)

# ------------------------------------------------------------
# Frame capture
# ------------------------------------------------------------
def get_frame_ffmpeg(url: str):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp"]
    if url.lower().startswith("rtsps://") and not tls_verify:
        cmd += ["-tls_verify", "0"]
    cmd += ["-i", url, "-an", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
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
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

# ------------------------------------------------------------
# Metrics / clipping helpers
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
    return float(max(0.0, min(1.0, 0.45 * s_size + 0.40 * s_sharp + 0.15 * s_con)))

def _pct(score):
    try:
        return int(round(float(score) * 100)) if score is not None else None
    except Exception:
        return None

def _bbox_overlap_ratio_with_zone(pts_full: np.ndarray, zone_box):
    try:
        pts = np.array(pts_full, dtype=np.float32).reshape(-1, 2)
        if pts.shape[0] != 4:
            return 0.0
        x1, y1, x2, y2 = map(float, zone_box)
        minx = float(np.min(pts[:, 0])); maxx = float(np.max(pts[:, 0]))
        miny = float(np.min(pts[:, 1])); maxy = float(np.max(pts[:, 1]))
        bw = max(1.0, maxx - minx)
        bh = max(1.0, maxy - miny)
        bbox_area = bw * bh
        ix1 = max(minx, x1); iy1 = max(miny, y1)
        ix2 = min(maxx, x2); iy2 = min(maxy, y2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        return float(inter / bbox_area) if bbox_area > 0 else 0.0
    except Exception:
        return 0.0

def _roi_clip_analysis(gray_roi: np.ndarray, margin_px: int = 6):
    h, w = gray_roi.shape[:2]
    m = max(1, int(margin_px))
    edge_thr = 0.02  # require at least 2% of edge band to be "dark" to count as touching

    out = {
        "clipped": False,
        "margin_px": int(margin_px),
        "bbox": None,
        "touch": {"left": False, "top": False, "right": False, "bottom": False},
        "dark_px": 0,
        "dark_frac": 0.0,
        "edge_dark_ratio": {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0},
    }
    try:
        # Otsu binarization (white-ish background)
        _, th = cv2.threshold(gray_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Ensure background is white (255) and "ink/modules" are black (0)
        dark0 = int(np.sum(th == 0))
        dark1 = int(np.sum(th == 255))
        if dark0 > dark1:
            th = 255 - th
            dark0, dark1 = dark1, dark0

        n = int(w * h) if w and h else 0
        out["dark_px"] = int(dark0)
        out["dark_frac"] = float(dark0) / float(max(1, n))

        # If almost nothing is dark, there's nothing to clip.
        if dark0 < 50 or out["dark_frac"] < 0.001:
            return out

        # If almost everything is dark, threshold likely failed (don't label "clipped").
        if out["dark_frac"] > 0.90:
            return out

        ys, xs = np.where(th == 0)
        if xs.size < 50:
            return out

        minx, maxx = int(xs.min()), int(xs.max())
        miny, maxy = int(ys.min()), int(ys.max())
        out["bbox"] = [minx, miny, maxx, maxy]

        # Edge-band density: prevents background mortar/tape lines from forcing "clipped"
        left_ratio = float(np.mean(th[:, :m] == 0))
        right_ratio = float(np.mean(th[:, max(0, w - m):] == 0))
        top_ratio = float(np.mean(th[:m, :] == 0))
        bottom_ratio = float(np.mean(th[max(0, h - m):, :] == 0))
        out["edge_dark_ratio"] = {
            "left": left_ratio, "top": top_ratio, "right": right_ratio, "bottom": bottom_ratio
        }

        out["touch"]["left"] = (minx <= m) and (left_ratio > edge_thr)
        out["touch"]["top"] = (miny <= m) and (top_ratio > edge_thr)
        out["touch"]["right"] = ((w - 1 - maxx) <= m) and (right_ratio > edge_thr)
        out["touch"]["bottom"] = ((h - 1 - maxy) <= m) and (bottom_ratio > edge_thr)

        out["clipped"] = any(out["touch"].values())
        return out
    except Exception:
        return out

# ------------------------------------------------------------
# Cutout + quiet-zone helper
# ------------------------------------------------------------
_QR_DETECTOR = cv2.QRCodeDetector()

def _tighten_bbox_to_dark(g: np.ndarray, x0: int, y0: int, x1: int, y1: int):
    """Tighten an axis-aligned crop box to the dark/module pixels inside it.

    This is useful for small QRs where the detector bbox can include extra paper.
    We intentionally crop tight to modules, then a synthetic white quiet-zone is added later.
    """
    try:
        h, w = g.shape[:2]
        x0 = int(max(0, min(w - 1, x0)))
        y0 = int(max(0, min(h - 1, y0)))
        x1 = int(max(x0 + 1, min(w, x1)))
        y1 = int(max(y0 + 1, min(h, y1)))
        sub = g[y0:y1, x0:x1]
        if sub.size == 0:
            return x0, y0, x1, y1

        sb = cv2.GaussianBlur(sub, (3, 3), 0)
        _, th = cv2.threshold(sb, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Ensure background is white (255) and modules are black (0)
        if int(np.sum(th == 0)) > int(np.sum(th == 255)):
            th = 255 - th

        dark = (th == 0).astype(np.uint8) * 255

        # For tiny codes, connect modules slightly so bbox isn't fragmented.
        k = 3 if min(sub.shape[:2]) < 140 else 5
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=1)

        ys, xs = np.where(dark == 255)
        if xs.size < 50:
            return x0, y0, x1, y1

        tx0 = x0 + int(xs.min())
        ty0 = y0 + int(ys.min())
        tx1 = x0 + int(xs.max()) + 1
        ty1 = y0 + int(ys.max()) + 1

        # clamp
        tx0 = int(max(0, min(w - 1, tx0)))
        ty0 = int(max(0, min(h - 1, ty0)))
        tx1 = int(max(tx0 + 1, min(w, tx1)))
        ty1 = int(max(ty0 + 1, min(h, ty1)))
        return tx0, ty0, tx1, ty1
    except Exception:
        return int(x0), int(y0), int(x1), int(y1)


def _qr_cutout_with_border(gray_roi: np.ndarray):
    """
    Cut out the QR region inside `gray_roi` as tightly as possible, then add a white
    quiet-zone border around it before decoding.

    Key design: we try OpenCV's QRCodeDetector *detect()* (localization only) on an upscaled
    ROI first. This tends to find even small QRs where binarization/CC fails. We then crop
    tightly to the detected quad's bounding box (no extra margin unless `cutout_*pad*` adds it).

    Returns: (padded_gray, meta)
      meta = {
        "used_candidate": bool,
        "method": str,
        "crop_box": [x0, y0, x1, y1],   # x1/y1 are exclusive, in ROI coords
        "border_px": int,
        "crop_shape": [h, w],
        "padded_shape": [h, w],
      }
    """
    h, w = gray_roi.shape[:2]
    meta = {
        "used_candidate": False,
        "method": "none",
        "crop_box": [0, 0, int(w), int(h)],
        "border_px": 0,
        "crop_shape": [int(h), int(w)],
        "padded_shape": [int(h), int(w)],
        "detect_quad": None,
    }

    if gray_roi is None or gray_roi.size == 0:
        return gray_roi, meta

    try:
        g = gray_roi
        if g.dtype != np.uint8:
            g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

        # --------------------------------------------------------
        # 1) Prefer OpenCV QR localization (detect only), upscaled.
        # --------------------------------------------------------
        if cutout_detect_enable:
            for s in (CUTOUT_DETECT_SCALES or [2.0, 3.0, 4.0]):
                try:
                    sf = float(s)
                    if sf <= 1.0:
                        continue
                    up = cv2.resize(g, (int(round(w * sf)), int(round(h * sf))), interpolation=cv2.INTER_CUBIC)
                    ok, pts = _QR_DETECTOR.detect(up)
                    if not ok or pts is None:
                        continue
                    pts = np.array(pts, dtype=np.float32).reshape(-1, 2) / float(sf)

                    minx = int(np.floor(np.min(pts[:, 0]))); maxx = int(np.ceil(np.max(pts[:, 0])))
                    miny = int(np.floor(np.min(pts[:, 1]))); maxy = int(np.ceil(np.max(pts[:, 1])))

                    # Convert to exclusive coords
                    x0 = max(0, min(w - 1, minx))
                    y0 = max(0, min(h - 1, miny))
                    x1 = max(x0 + 1, min(w, maxx + 1))
                    y1 = max(y0 + 1, min(h, maxy + 1))

                    cw = x1 - x0
                    ch = y1 - y0
                    area_bbox = int(cw * ch)
                    ar = float(cw) / float(max(1, ch))

                    # Basic sanity: reasonably square & not the whole ROI
                    if area_bbox < int(cutout_min_area):
                        continue
                    if int(min(cw, ch)) < int(cutout_min_side_px):
                        continue
                    if area_bbox > int(0.98 * float(w * h)):
                        continue
                    if not (float(cutout_ar_lo) <= ar <= float(cutout_ar_hi)):
                        continue

                    meta["detect_quad"] = pts.tolist()

                    # Tighten to dark/module pixels inside the detected bbox (removes paper margin)
                    if cutout_detect_tighten:
                        tx0, ty0, tx1, ty1 = _tighten_bbox_to_dark(g, x0, y0, x1, y1)
                        # adopt only if it stays sensible
                        if (tx1 - tx0) >= 8 and (ty1 - ty0) >= 8:
                            x0, y0, x1, y1 = tx0, ty0, tx1, ty1

                    # Optional tight pad (user wants *no* margin by default)
                    pad_in = int(round(float(cutout_inner_pad_frac) * float(min(cw, ch)))) + int(cutout_safety_pad_px)
                    if pad_in > 0:
                        x0 = max(0, x0 - pad_in)
                        y0 = max(0, y0 - pad_in)
                        x1 = min(w, x1 + pad_in)
                        y1 = min(h, y1 + pad_in)

                    meta["used_candidate"] = True
                    meta["method"] = f"qrdetect_{sf:.2f}x"
                    meta["crop_box"] = [int(x0), int(y0), int(x1), int(y1)]
                    meta["crop_shape"] = [int(y1 - y0), int(x1 - x0)]
                    break
                except Exception:
                    continue

        # --------------------------------------------------------
        # 2) Fallback: binarize + morphology + contour scoring.
        # --------------------------------------------------------
        if not meta["used_candidate"]:
            g_blur = cv2.GaussianBlur(g, (3, 3), 0)
            _, th = cv2.threshold(g_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # Ensure background is white and "ink/modules" are black (0)
            dark0 = int(np.sum(th == 0))
            dark1 = int(np.sum(th == 255))
            if dark0 > dark1:
                th = 255 - th
                dark0, dark1 = dark1, dark0

            n = int(w * h) if w and h else 0
            dark_frac = float(dark0) / float(max(1, n))
            meta["thr_method"] = "otsu"
            meta["thr_dark_frac"] = float(dark_frac)

            # If Otsu produces an extreme mask, try adaptive thresholding.
            if dark_frac < 0.01 or dark_frac > 0.80:
                try:
                    bs = 35 if min(h, w) >= 160 else 21
                    if bs >= min(h, w):
                        bs = max(11, (min(h, w) // 2) | 1)
                    thr = cv2.adaptiveThreshold(
                        g_blur, 255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        int(bs), 5
                    )
                    d0 = int(np.sum(thr == 0))
                    d1 = int(np.sum(thr == 255))
                    if d0 > d1:
                        thr = 255 - thr
                        d0, d1 = d1, d0
                    dfrac = float(d0) / float(max(1, n))
                    if 0.001 < dfrac < 0.95:
                        th = thr
                        meta["thr_method"] = f"adaptive{int(bs)}"
                        meta["thr_dark_frac"] = float(dfrac)
                except Exception:
                    pass

            # Raw dark mask (modules) and a "connected" version for contour finding.
            dark_raw = (th == 0).astype(np.uint8) * 255
            dark = dark_raw.copy()

            # Connect QR modules into a silhouette (prevents picking only a finder pattern)
            if cutout_close_iter > 0:
                kc = np.ones((int(cutout_close_ksize), int(cutout_close_ksize)), np.uint8)
                dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kc, iterations=int(cutout_close_iter))

            # Reduce thin background lines after closing
            if cutout_open_iter > 0:
                ko = np.ones((int(cutout_open_ksize), int(cutout_open_ksize)), np.uint8)
                dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, ko, iterations=int(cutout_open_iter))

            _fc = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cnts = _fc[0] if len(_fc) == 2 else _fc[1]

            best = None
            best_score = -1.0
            for c in cnts:
                x, y, cw, ch = cv2.boundingRect(c)
                area_bbox = int(cw * ch)
                if area_bbox < int(cutout_min_area):
                    continue
                if int(min(cw, ch)) < int(cutout_min_side_px):
                    continue
                if area_bbox > int(0.98 * float(w * h)):
                    continue

                ar = float(cw) / float(max(1, ch))
                if not (float(cutout_ar_lo) <= ar <= float(cutout_ar_hi)):
                    continue

                area_cont = float(cv2.contourArea(c))
                fill = area_cont / float(area_bbox) if area_bbox > 0 else 0.0
                ar_err = float(abs(ar - 1.0))
                score = float(area_bbox) * (1.0 - min(0.9, ar_err)) * (0.5 + min(1.0, fill))
                if score > best_score:
                    best_score = score
                    best = (x, y, cw, ch)

            if best is not None:
                x, y, cw, ch = best

                # Tighten: within the chosen silhouette bbox, re-bbox the RAW dark pixels.
                # This yields a crop "around the QR only" (no paper margin).
                sub = (dark_raw[y:y + ch, x:x + cw] == 255)
                ys, xs = np.where(sub)
                if xs.size >= 50:
                    tx0 = x + int(xs.min())
                    ty0 = y + int(ys.min())
                    tx1 = x + int(xs.max()) + 1
                    ty1 = y + int(ys.max()) + 1
                else:
                    tx0, ty0, tx1, ty1 = x, y, x + cw, y + ch

                cw2 = tx1 - tx0
                ch2 = ty1 - ty0

                pad_in = int(round(float(cutout_inner_pad_frac) * float(min(cw2, ch2)))) + int(cutout_safety_pad_px)
                if pad_in > 0:
                    tx0 = max(0, tx0 - pad_in)
                    ty0 = max(0, ty0 - pad_in)
                    tx1 = min(w, tx1 + pad_in)
                    ty1 = min(h, ty1 + pad_in)

                meta["used_candidate"] = True
                meta["method"] = "morph_cc"
                meta["crop_box"] = [int(tx0), int(ty0), int(tx1), int(ty1)]
                meta["crop_shape"] = [int(ty1 - ty0), int(tx1 - tx0)]

        # --------------------------------------------------------
        # Crop + (optional) whiten outside detected quad + add quiet-zone border
        # --------------------------------------------------------
        x0, y0, x1, y1 = meta["crop_box"]
        crop = g[y0:y1, x0:x1]
        if crop is None or crop.size == 0:
            crop = g
            meta["used_candidate"] = False
            meta["method"] = "none"
            meta["crop_box"] = [0, 0, int(w), int(h)]
            meta["crop_shape"] = [int(h), int(w)]

        # If we have a detected quad, force everything *outside* it to pure white.
        # This mirrors the (visual) "gap fill" but for the decode input, and it
        # often helps ZBar on noisy paper / low-contrast small codes.
        if bool(meta.get("used_candidate")) and cutout_whiten_outside_quad and meta.get("detect_quad") is not None:
            try:
                pts = np.array(meta.get("detect_quad"), dtype=np.float32).reshape(-1, 2)
                if pts.shape[0] == 4 and crop is not None and crop.size:
                    # Move quad into crop coordinates
                    pts[:, 0] -= float(x0)
                    pts[:, 1] -= float(y0)

                    # Clamp to crop bounds (fillPoly tolerates out-of-bounds but clamp helps stability)
                    ch, cw = crop.shape[:2]
                    pts[:, 0] = np.clip(pts[:, 0], -2.0, float(cw + 1))
                    pts[:, 1] = np.clip(pts[:, 1], -2.0, float(ch + 1))

                    mask = np.zeros((ch, cw), dtype=np.uint8)
                    cv2.fillPoly(mask, [pts.astype(np.int32)], 255)

                    if mask.size and np.any(mask == 0):
                        # Outside the quad -> white
                        crop2 = crop.copy()
                        crop2[mask == 0] = 255
                        crop = crop2
                        meta["whiten_outside_quad"] = True
            except Exception:
                pass

        if bool(meta.get("used_candidate")):
            border = max(
                int(cutout_border_min_px),
                int(round(float(cutout_border_frac) * float(min(crop.shape[:2])))))
        else:
            border = int(cutout_border_min_px)

        meta["border_px"] = int(border)

        if border > 0:
            padded = cv2.copyMakeBorder(
                crop, border, border, border, border,
                borderType=cv2.BORDER_CONSTANT, value=255
            )
        else:
            padded = crop

        meta["padded_shape"] = [int(padded.shape[0]), int(padded.shape[1])]
        return padded, meta

    except Exception:
        return gray_roi, meta

def _mark_clipping(gray_img: np.ndarray, analysis: dict):
    vis = cv2.cvtColor(gray_img, cv2.COLOR_GRAY2BGR)
    h, w = gray_img.shape[:2]
    bbox = analysis.get("bbox")
    touch = analysis.get("touch") or {}
    clipped = bool(analysis.get("clipped"))

    if bbox:
        minx, miny, maxx, maxy = bbox
        cv2.rectangle(vis, (minx, miny), (maxx, maxy), (0, 0, 255), 2)

    if touch.get("left"):
        cv2.line(vis, (0, 0), (0, h - 1), (0, 0, 255), 4)
    if touch.get("top"):
        cv2.line(vis, (0, 0), (w - 1, 0), (0, 0, 255), 4)
    if touch.get("right"):
        cv2.line(vis, (w - 1, 0), (w - 1, h - 1), (0, 0, 255), 4)
    if touch.get("bottom"):
        cv2.line(vis, (0, h - 1), (w - 1, h - 1), (0, 0, 255), 4)

    label = "roi_clipped" if clipped else "roi_ok"
    cv2.putText(vis, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
    return vis

def _failure_reason(zone_w: int, zone_h: int, lap_var: float, contrast: float, bright_clip: float, dark_clip: float, clipped: bool) -> str:
    if clipped:
        return "roi_clipped"
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
    return "decode_failed"

# ------------------------------------------------------------
# Preprocess variants
# ------------------------------------------------------------
def _preprocess_gray_variants(gray: np.ndarray):
    yield ("raw", gray)
    if try_invert:
        yield ("inv", 255 - gray)
    if not use_preprocess:
        return

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    yield ("clahe", clahe)
    if try_invert:
        yield ("clahe_inv", 255 - clahe)

    blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
    sharp = cv2.addWeighted(clahe, 1.8, blur, -0.8, 0)
    yield ("sharp", sharp)
    if try_invert:
        yield ("sharp_inv", 255 - sharp)

    for bs in adaptive_block_sizes:
        thr = cv2.adaptiveThreshold(
            sharp, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            int(bs), 5
        )
        yield (f"athr{bs}", thr)
        if try_invert:
            yield (f"athr{bs}_inv", 255 - thr)

    _, otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield ("otsu", otsu)
    if try_invert:
        yield ("otsu_inv", 255 - otsu)

# ------------------------------------------------------------
# ZBar helpers
# ------------------------------------------------------------
def _zbar_decode_roi(gray_roi: np.ndarray):
    if not (_ZBAR_OK and enable_zbar and zbar_decode):
        return []
    try:
        if zbar_qrcode_only and _ZBAR_QR_SYMBOL is not None:
            return zbar_decode(gray_roi, symbols=[_ZBAR_QR_SYMBOL]) or []
        res = zbar_decode(gray_roi) or []
        if zbar_qrcode_only:
            res = [r for r in res if getattr(r, "type", "") == "QRCODE"]
        return res
    except Exception:
        return []

def _zbar_poly_to_quad(poly_pts):
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
# OpenCV subprocess decode
# ------------------------------------------------------------
_OPENCV_SUBPROC_CODE = r"""
import sys, json, base64
import numpy as np
import cv2

b64 = sys.stdin.buffer.read().strip()
png = base64.b64decode(b64) if b64 else b""
img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE) if png else None
if img is None:
    print(json.dumps({"ok": False, "payload": "", "points": None, "method": "no_img"}))
    sys.exit(0)

q = cv2.QRCodeDetector()
payload, pts = q.detectAndDecode(img)
payload = (payload or "").strip()
if payload and pts is not None:
    print(json.dumps({"ok": True, "payload": payload, "points": pts.tolist(), "method": "detectAndDecode"}))
    sys.exit(0)

payload2, pts2 = q.detectAndDecodeCurved(img)
payload2 = (payload2 or "").strip()
if payload2 and pts2 is not None:
    print(json.dumps({"ok": True, "payload": payload2, "points": pts2.tolist(), "method": "detectAndDecodeCurved"}))
    sys.exit(0)

print(json.dumps({"ok": False, "payload": "", "points": pts.tolist() if pts is not None else None, "method": "no_decode"}))
"""

def _opencv_decode_subprocess(gray_img: np.ndarray):
    try:
        ok, buf = cv2.imencode(".png", gray_img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes())
        proc = subprocess.run(
            [sys.executable, "-c", _OPENCV_SUBPROC_CODE],
            input=b64,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=float(opencv_fallback_timeout_s),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return json.loads(proc.stdout.decode("utf-8", errors="ignore"))
    except Exception:
        return None

# ------------------------------------------------------------
# Scaling helper
# ------------------------------------------------------------
def _scaled_versions(gray_roi: np.ndarray, scale: float):
    if scale <= 1.000001:
        return [("1.00x", gray_roi)]
    h, w = gray_roi.shape[:2]
    nh = int(round(h * scale))
    nw = int(round(w * scale))
    m = max(nh, nw)
    eff = float(scale)
    if m > zone_max_scaled_dim:
        max_scale_allowed = float(zone_max_scaled_dim) / float(max(h, w))
        eff = max(1.0, min(float(scale), max_scale_allowed))
        nh = max(1, int(round(h * eff)))
        nw = max(1, int(round(w * eff)))

    return [
        (f"{eff:.2f}x_cubic", cv2.resize(gray_roi, (nw, nh), interpolation=cv2.INTER_CUBIC)),
        (f"{eff:.2f}x_near", cv2.resize(gray_roi, (nw, nh), interpolation=cv2.INTER_NEAREST)),
    ]

# ------------------------------------------------------------
# Debug state (in-memory)
# ------------------------------------------------------------
DEBUG_LOCK = threading.Lock()
DEBUG_STATE = {
    "zone": None,
    "ts": 0,
    "json": None,
    "roi_png": None,
    "roi_best_png": None,
    "roi_marked_png": None,
    "roi_best_marked_png": None,
}

def _set_debug(zone: str, debug_json: dict, roi: np.ndarray, roi_best: np.ndarray | None, marked_roi: np.ndarray | None, marked_best: np.ndarray | None):
    if not zone or zone != debug_zone:
        return
    with DEBUG_LOCK:
        DEBUG_STATE["zone"] = zone
        DEBUG_STATE["ts"] = int(time.time())
        DEBUG_STATE["json"] = debug_json
        DEBUG_STATE["roi_png"] = _encode_png(roi) if roi is not None else None
        DEBUG_STATE["roi_best_png"] = _encode_png(roi_best) if roi_best is not None else None
        DEBUG_STATE["roi_marked_png"] = _encode_png(marked_roi) if marked_roi is not None else None
        DEBUG_STATE["roi_best_marked_png"] = _encode_png(marked_best) if marked_best is not None else None

# ------------------------------------------------------------
# Zone scan
# ------------------------------------------------------------
def scan_zone(frame_gray: np.ndarray, zname: str, box, pad_px: int, scales: list[float]):
    H, W = frame_gray.shape[:2]
    try:
        zx1, zy1, zx2, zy2 = map(int, box)
    except Exception:
        return None, None

    x1p = max(0, zx1 - pad_px)
    y1p = max(0, zy1 - pad_px)
    x2p = min(W - 1, zx2 + pad_px)
    y2p = min(H - 1, zy2 + pad_px)
    if x2p <= x1p or y2p <= y1p:
        return None, None

    zone_w = max(1, zx2 - zx1)
    zone_h = max(1, zy2 - zy1)
    roi = frame_gray[y1p:y2p, x1p:x2p]
    if roi.size == 0:
        return None, None

    lap = _laplacian_var(roi)
    con = _contrast_std(roi)
    bright, dark = _clip_fractions(roi)

    clip_analysis_roi = _roi_clip_analysis(roi, margin_px=6)

    # Cut out QR-like component and add a white "quiet zone" border before decoding.
    if enable_cutout:
        roi_decode, cutout = _qr_cutout_with_border(roi)
    else:
        h0, w0 = roi.shape[:2]
        roi_decode = roi
        cutout = {
            "used_candidate": False,
            "crop_box": [0, 0, int(w0), int(h0)],
            "border_px": 0,
            "crop_shape": [int(h0), int(w0)],
            "padded_shape": [int(h0), int(w0)],
        }

    x0, y0, x1, y1 = [int(v) for v in (cutout.get("crop_box") or [0, 0, roi.shape[1], roi.shape[0]])]
    x0 = max(0, min(int(roi.shape[1]), x0))
    y0 = max(0, min(int(roi.shape[0]), y0))
    x1 = max(x0 + 1, min(int(roi.shape[1]), x1))
    y1 = max(y0 + 1, min(int(roi.shape[0]), y1))
    roi_crop = roi[y0:y1, x0:x1]
    cutout["crop_box"] = [x0, y0, x1, y1]
    border = int(cutout.get("border_px") or 0)

    # Clipping analysis:
    # - For overlay/debug, analyze the *decode image* (includes added white border),
    #   so we don't falsely flag "clipped" just because there was no quiet zone.
    clip_analysis = _roi_clip_analysis(roi_decode, margin_px=6) if roi_decode is not None and roi_decode.size else clip_analysis_roi

    # More meaningful "clipped" signal when cutout is used:
    # If the cutout bbox touches the *original ROI* edge, we likely cropped the QR.
    touch_roi_edge = False
    try:
        mpx = 6
        if bool(cutout.get("used_candidate")):
            touch_roi_edge = (x0 <= mpx) or (y0 <= mpx) or ((roi.shape[1] - x1) <= mpx) or ((roi.shape[0] - y1) <= mpx)
    except Exception:
        touch_roi_edge = False
    cutout["touch_roi_edge"] = bool(touch_roi_edge)

    clipped = (bool(touch_roi_edge) if bool(cutout.get("used_candidate")) else False) if enable_cutout else bool(clip_analysis_roi.get("clipped"))

    # If we have a detected quad (blue border), ZBar can sometimes decode best at 1.0x.
    # Many configs start at 2.0x; prepend 1.0x only for detected-quads to avoid extra work everywhere.
    scales_eff = list(scales) if isinstance(scales, (list, tuple)) else [float(scales)]
    try:
        if enable_cutout and (cutout.get("detect_quad") is not None):
            if 1.0 not in scales_eff:
                scales_eff = [1.0] + scales_eff
        # de-dup while keeping order
        _seen = set()
        scales_eff = [float(s) for s in scales_eff if not (float(s) in _seen or _seen.add(float(s)))]
    except Exception:
        scales_eff = list(scales) if isinstance(scales, (list, tuple)) else [float(scales)]

    dbg = {
        "zone": zname,
        "roi_shape": list(roi.shape),
        "roi_pad_px": pad_px,
        "roi_stats": {"lap_var": lap, "contrast": con, "bright_clip": bright, "dark_clip": dark},
        "cutout": cutout,
        "roi_decode_shape": list(roi_decode.shape) if roi_decode is not None else None,
        "roi_crop_shape": list(roi_crop.shape) if roi_crop is not None else None,
        "clip_analysis": clip_analysis,
        "clip_analysis_roi": clip_analysis_roi,
        "scales": scales_eff,
        "zbar": {"attempts": 0, "hits": 0},
        "opencv_subproc": {"attempts": 0, "hits": 0, "last": None},
        "best_preprocess": None,
    }

    best_pre = None

    # ZBar first
    for sc in scales_eff:
        for scale_tag, roi_s in _scaled_versions(roi_decode, sc):
            for pre_name, v in _preprocess_gray_variants(roi_s):
                dbg["zbar"]["attempts"] += 1
                if zname == debug_zone and best_pre is None:
                    best_pre = v.copy()
                    dbg["best_preprocess"] = {"scale": scale_tag, "pre": pre_name}

                res = _zbar_decode_roi(v)
                if not res:
                    continue
                dbg["zbar"]["hits"] += 1

                best_area = -1.0
                best_quad = None
                best_payload = None
                for r in res:
                    try:
                        payload = (r.data.decode("utf-8", errors="ignore") or "").strip()
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

                rh, rw = v.shape[:2]
                quad = best_quad.astype(np.float32)
                quad[:, 0] = np.clip(quad[:, 0], 0, rw - 1)
                quad[:, 1] = np.clip(quad[:, 1], 0, rh - 1)

                eff = float(scale_tag.split("x")[0])
                pts_roi = (quad / eff)
                pts_roi[:, 0] = pts_roi[:, 0] - float(border) + float(x0)
                pts_roi[:, 1] = pts_roi[:, 1] - float(border) + float(y0)
                pts_full = pts_roi + np.array([x1p, y1p], dtype=np.float32)
                cx = int(np.mean(pts_full[:, 0])); cy = int(np.mean(pts_full[:, 1]))

                if not (zx1 <= cx <= zx2 and zy1 <= cy <= zy2):
                    continue

                ov = _bbox_overlap_ratio_with_zone(pts_full, (zx1, zy1, zx2, zy2))
                if ov < float(zone_quad_in_zone_min_ratio):
                    continue

                edge_px = _edge_px_from_quad(pts_full)
                score = _certainty_score(edge_px, lap, con)

                det = {
                    "payload": best_payload,
                    "points": pts_full.tolist(),
                    "centroid": [cx, cy],
                    "zone": zname,
                    "score": score,
                    "diag": {"edge_px": edge_px, "lap_var": lap, "contrast": con, "src": "zbar", "zone_ov": ov},
                    "decoded": True,
                }

                marked_roi = _mark_clipping(roi, clip_analysis_roi)
                marked_best = _mark_clipping(best_pre, _roi_clip_analysis(best_pre, margin_px=6)) if best_pre is not None else None
                _set_debug(zname, dbg, roi, best_pre, marked_roi, marked_best)
                return det, None

    # fallback
    reason = _failure_reason(zone_w, zone_h, lap, con, bright, dark, clipped)
    if enable_cutout and (not bool(cutout.get("used_candidate"))) and reason == "decode_failed":
        reason = "no_candidate"
    score = _certainty_score(float(min(zone_w, zone_h)), lap, con)

    # If OpenCV localization found a QR quad (even if decoding failed), keep it as a candidate.
    cand_pts_full = None
    cand_edge_px = None
    cand_ov = None
    try:
        dq = cutout.get("detect_quad") if enable_cutout else None
        if dq is not None:
            pts_roi = np.array(dq, dtype=np.float32).reshape(-1, 2)
            if pts_roi.shape[0] == 4:
                # Clamp to ROI bounds
                pts_roi[:, 0] = np.clip(pts_roi[:, 0], 0, roi.shape[1] - 1)
                pts_roi[:, 1] = np.clip(pts_roi[:, 1], 0, roi.shape[0] - 1)
                pts_full = pts_roi + np.array([x1p, y1p], dtype=np.float32)

                cx_det = int(np.mean(pts_full[:, 0])); cy_det = int(np.mean(pts_full[:, 1]))
                ov_det = _bbox_overlap_ratio_with_zone(pts_full, (zx1, zy1, zx2, zy2))
                if (zx1 <= cx_det <= zx2 and zy1 <= cy_det <= zy2) and (ov_det >= float(zone_quad_in_zone_min_ratio)):
                    cand_pts_full = pts_full
                    cand_edge_px = _edge_px_from_quad(pts_full)
                    cand_ov = ov_det
                    score = _certainty_score(cand_edge_px, lap, con)
                    # If we have a quad but no decode, label it clearly.
                    if reason in ("decode_failed", "no_candidate", "too_small", "roi_clipped"):
                        reason = "detected_unresolved"
    except Exception:
        pass


    if opencv_subprocess_fallback and reason in ("decode_failed", "roi_clipped", "no_candidate", "detected_unresolved"):
        tries = 0
        for sc in sorted(set(scales_eff), reverse=True):
            for scale_tag, roi_s in _scaled_versions(roi_decode, sc):
                for pre_name, v in _preprocess_gray_variants(roi_s):
                    if tries >= opencv_fallback_attempts:
                        break
                    tries += 1
                    dbg["opencv_subproc"]["attempts"] += 1
                    out = _opencv_decode_subprocess(v)
                    dbg["opencv_subproc"]["last"] = {"scale": scale_tag, "pre": pre_name, "out": out}
                    if out and out.get("ok") and out.get("payload") and out.get("points"):
                        pts = np.array(out["points"], dtype=np.float32).reshape(-1, 2)
                        if pts.shape[0] != 4:
                            continue

                        eff = float(scale_tag.split("x")[0])
                        pts_roi = (pts / eff)
                        pts_roi[:, 0] = pts_roi[:, 0] - float(border) + float(x0)
                        pts_roi[:, 1] = pts_roi[:, 1] - float(border) + float(y0)
                        pts_full = pts_roi + np.array([x1p, y1p], dtype=np.float32)
                        cx = int(np.mean(pts_full[:, 0])); cy = int(np.mean(pts_full[:, 1]))
                        if not (zx1 <= cx <= zx2 and zy1 <= cy <= zy2):
                            continue
                        ov = _bbox_overlap_ratio_with_zone(pts_full, (zx1, zy1, zx2, zy2))
                        if ov < float(zone_quad_in_zone_min_ratio):
                            continue

                        edge_px = _edge_px_from_quad(pts_full)
                        score2 = _certainty_score(edge_px, lap, con)
                        dbg["opencv_subproc"]["hits"] += 1

                        det = {
                            "payload": (out["payload"] or "").strip(),
                            "points": pts_full.tolist(),
                            "centroid": [cx, cy],
                            "zone": zname,
                            "score": score2,
                            "diag": {"edge_px": edge_px, "lap_var": lap, "contrast": con, "src": "opencv_subproc", "zone_ov": ov, "method": out.get("method")},
                            "decoded": True,
                        }

                        marked_roi = _mark_clipping(roi, clip_analysis_roi)
                        marked_best = _mark_clipping(best_pre, _roi_clip_analysis(best_pre, margin_px=6)) if best_pre is not None else None
                        _set_debug(zname, dbg, roi, best_pre, marked_roi, marked_best)
                        return det, None
                if tries >= opencv_fallback_attempts:
                    break
            if tries >= opencv_fallback_attempts:
                break

    if cand_pts_full is not None:
        cxm = int(np.mean(cand_pts_full[:, 0])); cym = int(np.mean(cand_pts_full[:, 1]))
        miss_centroid = [cxm, cym]
        miss_points = cand_pts_full.tolist()
        miss_no_quad = False
        miss_diag = {"lap_var": lap, "contrast": con, "src": "qrdetect", "clipped": clipped, "edge_px": cand_edge_px, "zone_ov": cand_ov}
    else:
        miss_centroid = [int((zx1 + zx2) / 2), int((zy1 + zy2) / 2)]
        miss_points = None
        miss_no_quad = True
        miss_diag = {"lap_var": lap, "contrast": con, "src": "miss", "clipped": clipped}

    miss = {
        "payload": None,
        "points": miss_points,
        "centroid": miss_centroid,
        "zone": zname,
        "score": score,
        "reason": reason,
        "diag": miss_diag,
        "decoded": False,
        "no_quad": miss_no_quad,
    }

    marked_roi = _mark_clipping(roi, clip_analysis_roi)
    marked_best = _mark_clipping(best_pre, _roi_clip_analysis(best_pre, margin_px=6)) if best_pre is not None else None
    _set_debug(zname, dbg, roi, best_pre, marked_roi, marked_best)
    return None, miss

# ------------------------------------------------------------
# Detection entrypoint
# ------------------------------------------------------------
def detect_qr(frame_bgr: np.ndarray, zones_dict: dict):
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    zones_ok = isinstance(zones_dict, dict) and bool(zones_dict)

    if restrict_to_zones and not zones_ok:
        return [], True

    detections = []
    if zones_ok:
        pad = max(0, int(roi_padding_px))
        scales = _dedupe_sorted(ROI_SCALES + ZONE_EXTRA_SCALES) or [2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
        for zname, box in zones_dict.items():
            dec, miss = scan_zone(frame_gray, str(zname), box, pad, scales)
            if dec is not None:
                detections.append(dec)
            elif miss is not None:
                detections.append(miss)
        return detections, True if restrict_to_zones else False

    return [], False

# ------------------------------------------------------------
# Zone status
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
            cur = status[z]
            if cur["kind"] != "decoded":
                if cur["det"] is None or float(d.get("score") or 0.0) > float(cur["det"].get("score") or 0.0):
                    status[z] = {"kind": "candidate", "det": d}
    return status

# ------------------------------------------------------------
# Overlay rendering
# ------------------------------------------------------------
def draw_overlay(frame, detections, zones_dict):
    out = frame.copy()
    h, w = out.shape[:2]

    ORANGE = (0, 165, 255)
    RED = (0, 0, 255)
    BLUE = (255, 0, 0)
    OKBG = (0, 170, 0)
    MISSBG = (80, 80, 80)
    CAND_BG = (255, 255, 0)

    font = cv2.FONT_HERSHEY_SIMPLEX
    pad = 3

    zone_status = compute_zone_status(zones_dict, detections) if overlay_show_zone_status else {}

    # Fill the "gap" between the zone rectangle and an unresolved detected quad.
    # This is purely visual: it helps confirm how much non-QR area is inside the scan zone.
    if overlay_fill_unresolved_gap and overlay_show_unresolved_quads and isinstance(zones_dict, dict) and zones_dict and isinstance(zone_status, dict) and zone_status:
        alpha = float(overlay_fill_unresolved_alpha)
        alpha = max(0.0, min(0.85, alpha))

        for zname, st in zone_status.items():
            if not isinstance(st, dict) or st.get("kind") != "candidate":
                continue
            det = st.get("det") or {}
            if not isinstance(det, dict):
                continue
            if det.get("reason") != "detected_unresolved":
                continue
            pts_list = det.get("points")
            if not pts_list or zname not in zones_dict:
                continue

            try:
                x1, y1, x2, y2 = map(int, zones_dict[zname])
            except Exception:
                continue

            # Clamp and slice the zone region (avoid allocating full-frame masks on 16k frames).
            x1c = max(0, min(w - 1, x1))
            y1c = max(0, min(h - 1, y1))
            x2c = max(0, min(w - 1, x2))
            y2c = max(0, min(h - 1, y2))
            if x2c <= x1c or y2c <= y1c:
                continue

            pts = np.array(pts_list, dtype=np.int32).reshape(-1, 1, 2)
            if pts.shape[0] != 4:
                continue

            sub = out[y1c:y2c + 1, x1c:x2c + 1]
            sh, sw = sub.shape[:2]
            if sh < 2 or sw < 2:
                continue

            pts_sub = pts.copy()
            pts_sub[:, :, 0] -= int(x1c)
            pts_sub[:, :, 1] -= int(y1c)

            poly_mask = np.zeros((sh, sw), dtype=np.uint8)
            cv2.fillPoly(poly_mask, [pts_sub], 255)

            # Outside the quad (within the zone rectangle) -> fill with semi-transparent BLUE
            m = (poly_mask == 0)
            if not np.any(m):
                continue

            sub_f = sub.astype(np.float32)
            color = np.array(BLUE, dtype=np.float32).reshape(1, 1, 3)
            sub_f[m] = sub_f[m] * (1.0 - alpha) + color * alpha
            out[y1c:y2c + 1, x1c:x2c + 1] = sub_f.astype(np.uint8)

    if isinstance(zones_dict, dict) and zones_dict:
        for zname, box in zones_dict.items():
            try:
                x1, y1, x2, y2 = map(int, box)
            except Exception:
                continue

            cv2.rectangle(out, (x1, y1), (x2, y2), ORANGE, 2)

            zl = _safe_label(str(zname), 32)
            (tw, th), base = cv2.getTextSize(zl, font, 0.55, 2)
            zbx2 = min(w - 1, x1 + tw + pad * 2)
            zby2 = min(h - 1, y1 + th + base + pad * 2)
            cv2.rectangle(out, (x1, y1), (zbx2, zby2), ORANGE, -1)
            cv2.putText(out, zl, (x1 + pad, y1 + th + pad), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

            st = zone_status.get(zname, {"kind": "none", "det": None})
            det = st.get("det")

            if st.get("kind") == "decoded" and det:
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
                bg, fg = OKBG, (255, 255, 255)
            elif st.get("kind") == "candidate" and det:
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
                    parts.append(det.get("reason") or "unknown")
                text = " ".join(parts)
                bg, fg = CAND_BG, (0, 0, 0)
            else:
                text = "MISS"
                bg, fg = MISSBG, (255, 255, 255)

            text = _safe_label(text, 64)
            (stw, sth), sbase = cv2.getTextSize(text, font, 0.5, 1)
            sy1 = min(h - 1, zby2 + 2)
            sy2 = min(h - 1, sy1 + sth + sbase + pad * 2)
            sx2 = min(w - 1, x1 + stw + pad * 2)
            cv2.rectangle(out, (x1, sy1), (sx2, sy2), bg, -1)
            cv2.putText(out, text, (x1 + pad, sy1 + sth + pad), font, 0.5, fg, 1, cv2.LINE_AA)

    # Draw quads:
    # - decoded -> red
    # - detected-but-unresolved (has points but decoded=False) -> blue
    for d in detections:
        pts_list = d.get("points")
        if not pts_list or d.get("no_quad"):
            continue
        decoded = bool(d.get("decoded", False))
        if (not decoded) and (not overlay_show_unresolved_quads):
            continue

        pts = np.array(pts_list, dtype=np.int32).reshape(-1, 1, 2)
        if pts.shape[0] != 4:
            continue

        color = RED if decoded else BLUE
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=2)

        if decoded:
            label = d.get("payload") or "QR"
        else:
            label = "DETECTED"
        if overlay_show_scores:
            p = _pct(d.get("score"))
            if p is not None:
                label = f"{label} {p}%"
        if overlay_show_size_px:
            ep = _edge_px_from_det(d)
            if ep is not None:
                label = f"{label} {ep}px"
        label = _safe_label(label, 96)

        x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
        (tw, th), base = cv2.getTextSize(label, font, 0.6, 2)
        x1 = max(0, x)
        y1 = max(0, y - th - base - pad * 2)
        x2 = min(w - 1, x + tw + pad * 2)
        y2 = min(h - 1, y)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, -1)
        cv2.putText(out, label, (x + pad, y - pad), font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

    return out
# ------------------------------------------------------------
# HTTP server state + handler
# ------------------------------------------------------------
STATE_LOCK = threading.Lock()
STATE = {"ts": 0, "frame_png": None, "overlay_png": None, "detections": [], "last_frame_info": {}}

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
  <p>Overlay PNG: <code>/{OVERLAY_PNG_NAME}</code></p>
  <p>Debug zone: <code>{debug_zone or "-"}</code></p>
  <ul>
    <li><a href="/{OVERLAY_PNG_NAME}">overlay</a></li>
    <li><a href="/frame.png">frame</a></li>
    <li><a href="/detections.json">detections.json</a></li>
    <li><a href="/debug.json">debug.json</a></li>
    <li><a href="/debug/roi.png">debug roi</a></li>
    <li><a href="/debug/roi_best.png">debug roi_best</a></li>
    <li><a href="/debug/roi_marked.png">debug roi_marked</a></li>
    <li><a href="/debug/roi_best_marked.png">debug roi_best_marked</a></li>
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

        if path == "/debug.json":
            with DEBUG_LOCK:
                dj = DEBUG_STATE.get("json")
                dz = DEBUG_STATE.get("zone")
                dts = DEBUG_STATE.get("ts")
            body = json.dumps({"zone": dz, "ts": dts, "debug": dj}, ensure_ascii=False).encode("utf-8")
            return self._send(200, "application/json; charset=utf-8", body)

        if path == "/debug/roi.png":
            with DEBUG_LOCK:
                b = DEBUG_STATE.get("roi_png")
            if not b:
                return self._send(404, "text/plain; charset=utf-8", b"no debug roi available")
            return self._send(200, "image/png", b)

        if path == "/debug/roi_best.png":
            with DEBUG_LOCK:
                b = DEBUG_STATE.get("roi_best_png")
            if not b:
                return self._send(404, "text/plain; charset=utf-8", b"no debug roi_best available")
            return self._send(200, "image/png", b)

        if path == "/debug/roi_marked.png":
            with DEBUG_LOCK:
                b = DEBUG_STATE.get("roi_marked_png")
            if not b:
                return self._send(404, "text/plain; charset=utf-8", b"no debug roi_marked available")
            return self._send(200, "image/png", b)

        if path == "/debug/roi_best_marked.png":
            with DEBUG_LOCK:
                b = DEBUG_STATE.get("roi_best_marked_png")
            if not b:
                return self._send(404, "text/plain; charset=utf-8", b"no debug roi_best_marked available")
            return self._send(200, "image/png", b)

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
    "Starting QR Inventory (mode=%s interval=%ss required=%s overlay_png_name=%s restrict_to_zones=%s debug_zone=%s)",
    camera_mode, interval, required, OVERLAY_PNG_NAME, restrict_to_zones, debug_zone or "-"
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

        detections, zone_only_active = detect_qr(frame, zones)

        decoded = [d for d in detections if d.get("decoded", False) and d.get("payload") and d.get("zone")]
        by_payload = defaultdict(list)
        for d in decoded:
            by_payload[d["payload"]].append(d)

        for payload, items in by_payload.items():
            if len(items) > 1:
                def _rank(it):
                    sc = float(it.get("score") or 0.0)
                    ep = float((it.get("diag") or {}).get("edge_px") or 0.0)
                    return (sc, ep)
                best = sorted(items, key=_rank, reverse=True)[0]
                logger.warning("Payload conflict resolved: payload=%s choose=%s", payload, best.get("zone"))
                items = [best]

            d = items[0]
            zone = d["zone"]
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
            "miss": sum(1 for d in detections if not d.get("decoded", False)),
            "restrict_to_zones_active": zone_only_active,
            "debug_zone": debug_zone or None,
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