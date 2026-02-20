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

interval = int(opts.get('interval_seconds', 60))
required = int(opts.get('required_consistency', 3))
camera_mode = str(opts.get('camera_mode', 'rtsps')).lower()
stream_url = opts.get('rtsp_url')
tls_verify = bool(opts.get('tls_verify', False))

# HTTP overlay options
def _parse_port(v, default=8099):
    try:
        p = int(v)
        if 1 <= p <= 65535:
            return p
    except Exception:
        pass
    return default

HTTP_PORT = _parse_port(opts.get('http_port', 8099), default=8099)

_overlay_name = str(opts.get('overlay_png_name', 'overlay.png') or 'overlay.png')
_overlay_name = _overlay_name.strip().lstrip('/')
_overlay_name = os.path.basename(_overlay_name)  # prevent nested paths / traversal
if not _overlay_name:
    _overlay_name = 'overlay.png'
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += '.png'
OVERLAY_PNG_NAME = _overlay_name

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
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else None

def _safe_label(s: str, max_len: int = 96) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s

def draw_overlay(frame, detections):
    """
    Draw red frame around each detected QR code + payload text.
    OpenCV uses BGR, so red is (0,0,255).
    """
    out = frame.copy()

    for det in detections:
        pts = np.array(det.get("points", []), dtype=np.int32).reshape((-1, 1, 2))
        if pts.size == 0:
            continue

        # Red polygon frame
        cv2.polylines(out, [pts], isClosed=True, color=(0, 0, 255), thickness=2)

        # Label (payload)
        label = _safe_label(det.get("payload", ""))
        if not label:
            continue

        x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 2

        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)

        # background box for readability
        pad = 4
        x1, y1 = x, max(0, y - th - baseline - pad * 2)
        x2, y2 = min(out.shape[1] - 1, x + tw + pad * 2), y

        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), -1)  # filled red
        cv2.putText(
            out,
            label,
            (x + pad, y - pad),
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
        # route HTTP logs into addon logger (and keep it less noisy)
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
    "Starting QR Inventory (mode=%s interval=%ss required=%s tls_verify=%s http_port=%s overlay_png_name=%s)",
    camera_mode, interval, required, tls_verify, HTTP_PORT, OVERLAY_PNG_NAME
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

        retval, decoded_info, points, _ = qcd.detectAndDecodeMulti(frame)

        detections = []
        if retval and decoded_info is not None and points is not None:
            for info, pts in zip(decoded_info, points):
                if not info or pts is None:
                    continue

                pts = pts.reshape(-1, 2)
                cx = int(pts[:, 0].mean())
                cy = int(pts[:, 1].mean())

                zone = centroid_to_zone(cx, cy, zones)
                history[info].append(zone)

                detections.append({
                    "payload": info,
                    "points": pts.tolist(),   # 4 corners
                    "centroid": [cx, cy],
                    "zone": zone,
                })

                logger.info(
                    "Detected payload=%s centroid=(%d,%d) zone=%s history=%s",
                    info, cx, cy, zone, list(history[info])
                )

                if len(history[info]) >= history_maxlen and len(set(history[info])) == 1:
                    confirmed_zone = history[info][-1]
                    if confirmed_zone is not None:
                        persist_mapping(info, confirmed_zone)
        else:
            logger.debug("No QR codes detected")

        # Update overlay state (serve latest frame even if no detections)
        overlay = draw_overlay(frame, detections)
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