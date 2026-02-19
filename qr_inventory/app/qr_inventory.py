# QR Inventory Add-on Hauptskript (RTSP only)
import time, json, os, logging
from collections import deque, defaultdict

import cv2
import numpy as np  # noqa: F401

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('qr_inventory')

# Lade Optionen aus /data/options.json
opts_path = '/data/options.json'
if os.path.exists(opts_path):
    with open(opts_path, 'r') as f:
        opts = json.load(f)
else:
    logger.warning('options.json nicht gefunden, verwende Standardwerte')
    opts = {}

interval = int(opts.get('interval_seconds', 60))
required = int(opts.get('required_consistency', 3))
camera_mode = opts.get('camera_mode', 'rtsp')
rtsp_url = opts.get('rtsp_url')

# zones kommt aus config.yaml als JSON-String ("{}"), kann aber auch dict sein
zones_raw = opts.get('zones', {})
if isinstance(zones_raw, str):
    try:
        zones = json.loads(zones_raw) if zones_raw.strip() else {}
    except Exception:
        logger.warning('zones ist kein gültiges JSON, verwende {}')
        zones = {}
elif isinstance(zones_raw, dict):
    zones = zones_raw
else:
    zones = {}

# Hilfsfunktion: Zone anhand von Koordinaten bestimmen
def centroid_to_zone(cx, cy, zones_dict):
    for name, box in zones_dict.items():
        try:
            x1, y1, x2, y2 = box
        except Exception:
            continue
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return name
    return None

# Zustand: history pro Payload
history_maxlen = required if required and required > 0 else 1
history = defaultdict(lambda: deque(maxlen=history_maxlen))
confirmed = {}

qcd = cv2.QRCodeDetector()

def get_frame_rtsp(url):
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        logger.error('RTSP Stream nicht erreichbar: %s', url)
        return None
    ret, frame = cap.read()
    cap.release()
    if not ret:
        logger.error('Kein Frame vom RTSP Stream erhalten')
        return None
    return frame

# Persistenz: inventory.json in /data
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
        logger.exception('Fehler beim Schreiben der Inventory Datei: %s', e)

# Hauptloop
logger.info('Starte QR Inventory Add-on (RTSP only, interval=%s, required=%s)', interval, required)
while True:
    try:
        if camera_mode != 'rtsp':
            logger.error('Nur RTSP Modus wird unterstützt. Setze camera_mode auf "rtsp".')
            time.sleep(interval)
            continue

        if not rtsp_url:
            logger.error('rtsp_url ist leer. Bitte in den Add-on Optionen setzen.')
            time.sleep(interval)
            continue

        frame = get_frame_rtsp(rtsp_url)
        if frame is None:
            time.sleep(interval)
            continue

        retval, decoded_info, points, _ = qcd.detectAndDecodeMulti(frame)
        if retval and decoded_info is not None and points is not None:
            for info, pts in zip(decoded_info, points):
                if not info:
                    continue
                pts = pts.reshape(-1, 2)
                cx = int(pts[:, 0].mean())
                cy = int(pts[:, 1].mean())
                zone = centroid_to_zone(cx, cy, zones)
                history[info].append(zone)
                logger.info(
                    'Detected payload=%s centroid=(%d,%d) zone=%s history=%s',
                    info, cx, cy, zone, list(history[info])
                )
                if len(history[info]) >= history_maxlen and len(set(history[info])) == 1:
                    confirmed_zone = history[info][-1]
                    if confirmed_zone is not None:
                        persist_mapping(info, confirmed_zone)
        else:
            logger.debug('Keine QR Codes erkannt')

    except Exception as e:
        logger.exception('Fehler in Hauptloop: %s', e)

    time.sleep(interval)
