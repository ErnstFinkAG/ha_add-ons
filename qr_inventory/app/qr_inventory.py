import time
import json
import os
import logging
import subprocess
from collections import deque, defaultdict

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('qr_inventory')

opts_path = '/data/options.json'
if os.path.exists(opts_path):
    with open(opts_path, 'r') as f:
        opts = json.load(f)
else:
    logger.warning('options.json not found, using defaults')
    opts = {}

interval = int(opts.get('interval_seconds', 60))
required = int(opts.get('required_consistency', 3))
camera_mode = opts.get('camera_mode', 'rtsps')
stream_url = opts.get('rtsp_url')
tls_verify = bool(opts.get('tls_verify', False))

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

inv_path = '/data/inventory.json'
if os.path.exists(inv_path):
    try:
        with open(inv_path, 'r') as f:
            confirmed = json.load(f)
    except Exception:
        confirmed = {}

def persist_mapping(payload, zone):
    prev = confirmed.get(payload)
    if prev == zone:
        return
    confirmed[payload] = zone
    try:
        with open(inv_path, 'w') as f:
            json.dump(confirmed, f, indent=2)
        logger.info('Persisted mapping %s -> %s', payload, zone)
    except Exception as e:
        logger.exception('Failed writing inventory.json: %s', e)

logger.info(
    "Starting QR Inventory (mode=%s interval=%ss required=%s tls_verify=%s)",
    camera_mode, interval, required, tls_verify
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

        if retval and decoded_info is not None and points is not None:
            for info, pts in zip(decoded_info, points):
                if not info or pts is None:
                    continue
                pts = pts.reshape(-1, 2)
                cx = int(pts[:, 0].mean())
                cy = int(pts[:, 1].mean())

                zone = centroid_to_zone(cx, cy, zones)
                history[info].append(zone)

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

    except Exception as e:
        logger.exception("Error in main loop: %s", e)

    time.sleep(interval)
