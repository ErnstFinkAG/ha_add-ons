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

interval = _opt_int('interval_seconds', 60)
required = _opt_int('required_consistency', 3)
camera_mode = str(opts.get('camera_mode', 'rtsps')).lower()
stream_url = opts.get('rtsp_url')
tls_verify = _opt_bool('tls_verify', False)

# Detection accuracy options
zone_fallback = _opt_bool("zone_fallback", True)     # scan each zone ROI as fallback
use_preprocess = _opt_bool("use_preprocess", True)   # CLAHE/sharpen/adaptive threshold variants
roi_padding_px = _opt_int("roi_padding_px", 40)      # padding around zone crops
roi_scale = _opt_float("roi_scale", 2.0)             # upscale factor for zone crops (>1 helps small/far codes)

# Overlay PNG name (configurable)
_overlay_name = str(opts.get('overlay_png_name', 'overlay.png') or 'overlay.png')
_overlay_name = _overlay_name.strip().lstrip('/')
_overlay_name = os.path.basename(_overlay_name)  # prevent nested paths / traversal
if not _overlay_name:
    _overlay_name = 'overlay.png'
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += '.png'
OVERLAY_PNG_NAME = _overlay_name

# HTTP server binds to a FIXED internal container port.
# Change the host port mapping in the add-on UI (Network tab) if you want a different external port.
HTTP_PORT = 8099

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
# Frame capture via ffmpeg
# ------------------------------------------------------------
def get_frame_ffmpeg(url: str):
    """
    Grab one frame via ffmpeg (works better with rtsps:// than cv2.VideoCapture).
    This build of ffmpeg does NOT support -stimeout or -rw_timeout,
    so we rely on subprocess timeout instead.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
    ]

    # Disable TLS verify for self-signed RTSPS (if supported by ffmpeg build)
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
# Robust QR detection (higher accuracy)
# ------------------------------------------------------------
def _quad_area(pts: np.ndarray) -> float:
    try:
        return abs(cv2.contourArea(pts.astype(np.float32)))
    except Exception:
        return 0.0

def _run_qr_detector(img):
    """
    Returns list of (payload, pts[4x2]) for successfully decoded QR codes.
    img can be grayscale or thresholded 8-bit.
    """
    try:
        retval, decoded_info, points, _ = qcd.detectAndDecodeMulti(img)
    except Exception:
        return []

    out = []
    if not retval or decoded_info is None or points is None:
        return out

    for payload, pts in zip(decoded_info, points):
        if not payload or pts is None:
            continue
        pts = np.array(pts, dtype=np.float32).reshape(-1, 2)
        if pts.shape[0] != 4:
            continue
        out.append((payload, pts))
    return out

def _preprocess_variants(frame_bgr: np.ndarray):
    """
    Yield images to try in the detector.
    Keep these 8-bit single-channel where possible (works well for QR).
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    yield gray

    if not use_preprocess:
        return

    # Contrast boost (helps IR / low contrast paper)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    yield clahe

    # Unsharp mask (improves edges)
    blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
    sharp = cv2.addWeighted(clahe, 1.7, blur, -0.7, 0)
    yield sharp

    # Adaptive threshold (helps uneven lighting)
    thr = cv2.adaptiveThreshold(
        sharp, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35, 5
    )
    yield thr

def detect_qr_robust(frame_bgr: np.ndarray, zones_dict: dict):
    """
    Returns detections list:
      {payload, points, centroid, zone}
    points are in original-frame coordinates.
    """
    h, w = frame_bgr.shape[:2]
    best = {}  # payload -> {"pts": np.ndarray, "area": float}

    # Pass 1: full-frame on multiple variants
    for variant in _preprocess_variants(frame_bgr):
        for payload, pts in _run_qr_detector(variant):
            area = _quad_area(pts)
            prev = best.get(payload)
            if prev is None or area > prev["area"]:
                best[payload] = {"pts": pts, "area": area}

    # Pass 2: per-zone ROI fallback (catches small/far codes)
    if zone_fallback and isinstance(zones_dict, dict) and zones_dict:
        base_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        scale = roi_scale if roi_scale and roi_scale > 1.0 else 1.0
        pad = max(0, roi_padding_px)

        for zname, box in zones_dict.items():
            try:
                x1, y1, x2, y2 = map(int, box)
            except Exception:
                continue

            # Add padding and clamp
            x1p = max(0, x1 - pad)
            y1p = max(0, y1 - pad)
            x2p = min(w - 1, x2 + pad)
            y2p = min(h - 1, y2 + pad)
            if x2p <= x1p or y2p <= y1p:
                continue

            roi = base_gray[y1p:y2p, x1p:x2p]
            if roi.size == 0:
                continue

            if scale != 1.0:
                roi_big = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            else:
                roi_big = roi

            roi_imgs = [roi_big]
            if use_preprocess:
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(roi_big)
                blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
                sharp = cv2.addWeighted(clahe, 1.7, blur, -0.7, 0)
                thr = cv2.adaptiveThreshold(
                    sharp, 255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    35, 5
                )
                roi_imgs += [clahe, sharp, thr]

            for img in roi_imgs:
                for payload, pts in _run_qr_detector(img):
                    # map ROI coords back to full-frame coords
                    pts_full = (pts / scale) + np.array([x1p, y1p], dtype=np.float32)
                    area = _quad_area(pts_full)
                    prev = best.get(payload)
                    if prev is None or area > prev["area"]:
                        best[payload] = {"pts": pts_full, "area": area}

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
}

def _encode_png(img):
    # PNG encoding is CPU heavy; compression 1 is usually a good speed/size compromise.
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes() if ok else None

def _safe_label(s: str, max_len: int = 96) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s

def draw_overlay(frame, detections, zones_dict):
    """
    Draw:
      - zones (orange rectangles + zone name)
      - QR frames (red polygon) + payload text
    OpenCV uses BGR:
      - red    = (0, 0, 255)
      - orange = (0, 165, 255)
    """
    out = frame.copy()
    h, w = out.shape[:2]

    # Zones first (so QR boxes appear on top)
    ORANGE = (0, 165, 255)
    if isinstance(zones_dict, dict) and zones_dict:
        for zname, box in zones_dict.items():
            try:
                x1, y1, x2, y2 = map(int, box)
            except Exception:
                continue

            # Clamp to image bounds
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

                # draw label inside the zone at top-left
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

        label = _safe_label(det.get("payload", ""))
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

        if path in ("/", "/index.html"):
            html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>QR Inventory Overlay</title></head>
<body style="font-family: sans-serif">
  <h3>QR Inventory Overlay</h3>
  <p>Overlay PNG: <code>/{OVERLAY_PNG_NAME}</code> (alias: <code>/overlay.png</code>)</p>
  <p>Last update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}</p>
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
            body = json.dumps({"ts": ts, "detections": det}, ensure_ascii=False).encode("utf-8")
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
    "zone_fallback=%s use_preprocess=%s roi_padding_px=%s roi_scale=%s)",
    camera_mode, interval, required, tls_verify, OVERLAY_PNG_NAME,
    zone_fallback, use_preprocess, roi_padding_px, roi_scale
)

while True:
    try:
        if camera_mode not in ("rtsp", "rtsps"):
            logger.error('Unsupported camera_mode=%s (use rtsp or rtsps)', camera_mode)
            time.sleep(interval)
            continue

        if not stream_url:
            logger.error('rtsp_url is empty. Please set it in the add-on options.')
            time.sleep(interval)
            continue

        frame = get_frame_ffmpeg(stream_url)
        if frame is None:
            time.sleep(interval)
            continue

        # Robust, higher-accuracy detection
        detections = detect_qr_robust(frame, zones)

        for det in detections:
            info = det["payload"]
            cx, cy = det["centroid"]
            zone = det["zone"]

            history[info].append(zone)

            logger.info(
                "Detected payload=%s centroid=(%d,%d) zone=%s history=%s",
                info, cx, cy, zone, list(history[info])
            )

            if len(history[info]) >= history_maxlen and len(set(history[info])) == 1:
                confirmed_zone = history[info][-1]
                if confirmed_zone is not None:
                    persist_mapping(info, confirmed_zone)

        # Update overlay state
        overlay = draw_overlay(frame, detections, zones)
        frame_png = _encode_png(frame)
        overlay_png = _encode_png(overlay)

        with STATE_LOCK:
            STATE["ts"] = int(time.time())
            STATE["frame_png"] = frame_png
            STATE["overlay_png"] = overlay_png
            STATE["detections"] = detections

    except Exception as e:
        logger.exception("Error in main loop: %s", e)

    time.sleep(interval)