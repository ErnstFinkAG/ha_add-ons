import time
import json
import os
import sys
import base64
import html
import logging
import re
import subprocess
import threading
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs, quote

import cv2
import numpy as np

# Optional MQTT
try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion
    _MQTT_OK = True
except Exception:
    mqtt = None
    CallbackAPIVersion = None
    _MQTT_OK = False

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

def _deep_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k not in cur:
            return default
        cur = cur.get(k)
    return cur

def _parse_int(v, default):
    try:
        return int(v)
    except Exception:
        return default

def _parse_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default

def _parse_str_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # allow comma-separated strings
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [s]
    return [str(v).strip()] if str(v).strip() else []

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


def _normalize_hex_color(v, default="FFFFFF"):
    s = str(v or default).strip()
    if s.startswith("#"):
        s = s[1:]
    s = s.upper()
    return s if re.fullmatch(r"[0-9A-F]{6}", s or "") else str(default).strip().lstrip("#").upper()


def _hex_rgb_to_bgr_tuple(hex_rgb: str):
    s = _normalize_hex_color(hex_rgb, "FFFFFF")
    return (int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16))


overlay_alignment_enabled = _opt_bool("overlay_alignment_enabled", False)
overlay_alignment_color = _normalize_hex_color(opts.get("overlay_alignment_color", "FFFFFF"), "FFFFFF")
overlay_alignment_direction = str(opts.get("overlay_alignment_direction", "both") or "both").strip().lower()
if overlay_alignment_direction not in ("horizontal", "vertical", "both"):
    overlay_alignment_direction = "both"
overlay_alignment_width = max(1, _opt_int("overlay_alignment_width", 2))
overlay_margin_enabled = _opt_bool("overlay_margin_enabled", False)
overlay_margin_px = max(0, _opt_int("overlay_margin_px", 10))
overlay_detected_list_enabled = _opt_bool("detected_list_enabled", False)
detected_list_regex = str(opts.get("detected_list_regex") or "").strip()
DETECTED_LIST_REGEX_TEXT = detected_list_regex
detected_list_sort_order = str(opts.get("detected_list_sort_order") or "asc").strip().lower()
if detected_list_sort_order not in ("asc", "desc"):
    detected_list_sort_order = "asc"
DETECTED_LIST_SORT_ORDER = detected_list_sort_order
try:
    DETECTED_LIST_REGEX = re.compile(detected_list_regex) if detected_list_regex else None
except re.error as e:
    logger.error("Invalid detected_list_regex %r: %s", detected_list_regex, e)
    DETECTED_LIST_REGEX = None
OVERLAY_ALIGNMENT_COLOR_BGR = _hex_rgb_to_bgr_tuple(overlay_alignment_color)

STREAM_INFO_INTERVAL_MINUTES_DEFAULT = max(0, _opt_int("stream_info_interval_minutes", 0))

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
# Global defaults (multi-camera)
# ------------------------------------------------------------
_defaults = opts.get("defaults") if isinstance(opts.get("defaults"), dict) else {}

DEFAULT_INTERVAL_S = _parse_int(_defaults.get("interval_s", 60), 60)
DEFAULT_REQUIRED = _parse_int(_defaults.get("required", 3), 3)
DEFAULT_RESTRICT_TO_ZONES = _parse_bool(_defaults.get("restrict_to_zones", False), False)

# TLS verify: global default, can be overridden per camera.stream.tls_verify
TLS_VERIFY_DEFAULT = _parse_bool(opts.get("tls_verify", False), False)

# Logging
_log_level = str(_defaults.get("log_level", opts.get("log_level", "info"))).strip().lower()
_LOG_LEVEL_MAP = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}
logger.setLevel(_LOG_LEVEL_MAP.get(_log_level, logging.INFO))

# Debug zones (GLOBAL): list of zone names like ["A1","A2","Y1"].
# Use ["*"] to debug all zones on all cameras.
_debug_zones_raw = opts.get("debug_zones", [])
DEBUG_ZONES = set(_parse_str_list(_debug_zones_raw))
DEBUG_ALL_ZONES = ("*" in DEBUG_ZONES)
if DEBUG_ALL_ZONES:
    DEBUG_ZONES.discard("*")

def _debug_enabled_for_zone(zone_name: str) -> bool:
    if DEBUG_ALL_ZONES:
        return True
    return str(zone_name) in DEBUG_ZONES

# ------------------------------------------------------------
# Overlay HTTP server settings
# ------------------------------------------------------------
HTTP_PORT = _parse_int(opts.get("overlay_http_port", 8099), 8099)

OVERLAY_ROUTE_PREFIX = str(opts.get("overlay_route_prefix", "/overlays") or "/overlays").strip()
if not OVERLAY_ROUTE_PREFIX.startswith("/"):
    OVERLAY_ROUTE_PREFIX = "/" + OVERLAY_ROUTE_PREFIX
OVERLAY_ROUTE_PREFIX = OVERLAY_ROUTE_PREFIX.rstrip("/")

FRAME_ROUTE_PREFIX = str(opts.get("frame_route_prefix", "/frames") or "/frames").strip()
if not FRAME_ROUTE_PREFIX.startswith("/"):
    FRAME_ROUTE_PREFIX = "/" + FRAME_ROUTE_PREFIX
FRAME_ROUTE_PREFIX = FRAME_ROUTE_PREFIX.rstrip("/")

# Legacy single-camera overlay filename (still served for backward compatibility)
_overlay_name = os.path.basename(_opt_str('overlay_png_name', 'overlay.png').strip().lstrip('/')) or "overlay.png"
if not _overlay_name.lower().endswith('.png'):
    _overlay_name += ".png"
OVERLAY_PNG_NAME = _overlay_name

# ------------------------------------------------------------
# MQTT settings
# ------------------------------------------------------------
_mqtt_opts = opts.get("mqtt") if isinstance(opts.get("mqtt"), dict) else {}

MQTT_ENABLED = _parse_bool(_mqtt_opts.get("enabled", False), False)
MQTT_HOST = str(_mqtt_opts.get("host") or "").strip()
MQTT_PORT = max(1, _parse_int(_mqtt_opts.get("port", 1883), 1883))
MQTT_USERNAME = str(_mqtt_opts.get("username") or "").strip()
MQTT_PASSWORD = _mqtt_opts.get("password")
MQTT_CLIENT_ID = str(_mqtt_opts.get("client_id") or "qr_inventory").strip() or "qr_inventory"
MQTT_TOPIC_PREFIX = str(_mqtt_opts.get("topic_prefix") or "qr_inventory").strip().strip("/") or "qr_inventory"
MQTT_DISCOVERY_PREFIX = str(_mqtt_opts.get("discovery_prefix") or "homeassistant").strip().strip("/") or "homeassistant"
MQTT_RETAIN = _parse_bool(_mqtt_opts.get("retain", True), True)
MQTT_KEEPALIVE = max(15, _parse_int(_mqtt_opts.get("keepalive", 60), 60))
MQTT_QOS = 1
MQTT_STATE_NONE = "none"
MQTT_STATE_DETECTED_NO_VALUE = "detected_no_value"
MQTT_AVAILABILITY_TOPIC = f"{MQTT_TOPIC_PREFIX}/status"
MQTT_HA_BIRTH_TOPIC = f"{MQTT_DISCOVERY_PREFIX}/status"


def _slugify_token(value: str, default: str = "item", lower: bool = False) -> str:
    s = str(value or "").strip()
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if lower:
        s = s.lower()
    return s or str(default or "item")


def _mqtt_sensor_name(cam_name: str, zone_name: str) -> str:
    cam = str(cam_name or "").strip() or "camera"
    zone = str(zone_name or "").strip() or "zone"
    return f"{cam}_{zone}"


def _natural_sort_key(value: str):
    s = str(value or "")
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", s)]


def _sort_naturally(values, reverse: bool = False):
    return sorted(values, key=_natural_sort_key, reverse=bool(reverse))


def _detected_list_reverse_sort() -> bool:
    return str(DETECTED_LIST_SORT_ORDER or "asc").lower() == "desc"


def _extract_detected_list_key(payload: str) -> str:
    payload = str(payload or "").strip()
    if not payload:
        return ""
    rx = DETECTED_LIST_REGEX
    if rx is None:
        return payload
    try:
        m = rx.search(payload)
    except Exception:
        return payload
    if not m:
        return payload
    groups = [g for g in m.groups() if g is not None and str(g).strip()]
    if groups:
        return str(groups[0]).strip()
    matched = (m.group(0) or "").strip()
    return matched or payload


def _choose_detected_list_label(group_key: str, payloads) -> str:
    vals = _sort_naturally({str(p).strip() for p in (payloads or []) if str(p).strip()}, reverse=_detected_list_reverse_sort())
    if not vals:
        return str(group_key or "")
    gk = str(group_key or "").strip()
    starts = [p for p in vals if gk and p.lower().startswith(gk.lower())]
    if starts:
        starts = sorted(starts, key=lambda s: (len(s), _natural_sort_key(s)), reverse=_detected_list_reverse_sort())
        return starts[0]
    contains = [p for p in vals if gk and gk.lower() in p.lower()]
    if contains:
        contains = sorted(contains, key=lambda s: (len(s), _natural_sort_key(s)), reverse=_detected_list_reverse_sort())
        return contains[0]
    return vals[0]


def build_detected_list_summary(states: dict):
    groups = {}
    if isinstance(states, dict):
        for cam_id, st in states.items():
            detections = (st or {}).get("detections") or []
            for det in detections:
                if not isinstance(det, dict):
                    continue
                if not bool(det.get("decoded", False)):
                    continue
                payload = str(det.get("payload") or "").strip()
                zone = str(det.get("zone") or "").strip()
                if not payload or not zone:
                    continue
                det_cam_id = str(det.get("camera") or cam_id or "").strip() or str(cam_id or "cam")
                group_key = _extract_detected_list_key(payload)
                if not group_key:
                    group_key = payload
                item = groups.setdefault(group_key, {
                    "group_key": group_key,
                    "payloads": set(),
                    "zones": set(),
                    "locations": set(),
                    "camera_ids": set(),
                })
                item["payloads"].add(payload)
                item["zones"].add(zone)
                item["locations"].add(_camera_location(det_cam_id, zone))
                item["camera_ids"].add(det_cam_id)

    reverse_sort = _detected_list_reverse_sort()
    items = []
    for group_key, item in groups.items():
        payloads = _sort_naturally(item.get("payloads") or [], reverse=reverse_sort)
        zones = _sort_naturally(item.get("zones") or [], reverse=reverse_sort)
        locations = _sort_naturally(item.get("locations") or [], reverse=reverse_sort)
        camera_ids = _sort_naturally(item.get("camera_ids") or [], reverse=reverse_sort)
        label = _choose_detected_list_label(group_key, payloads)
        items.append({
            "group_key": str(group_key),
            "label": label,
            "members": zones,
            "locations": locations,
            "camera_ids": camera_ids,
            "payloads": payloads,
            "member_count": len(zones),
            "location_count": len(locations),
        })

    items.sort(
        key=lambda x: (_natural_sort_key(x.get("group_key") or ""), _natural_sort_key(x.get("label") or "")),
        reverse=reverse_sort,
    )
    lines = []
    for item in items:
        members = item.get("members") or []
        label = str(item.get("label") or item.get("group_key") or "")
        lines.append(f"{label} : {', '.join(members)}" if members else label)

    return {
        "ts": int(time.time()),
        "count": len(items),
        "regex": DETECTED_LIST_REGEX_TEXT or None,
        "sort_order": DETECTED_LIST_SORT_ORDER,
        "items": items,
        "lines": lines,
        "text": "\n".join(lines),
    }

def _print_styles_css() -> str:
    return """
:root { color-scheme: light; }
body { font-family: Arial, Helvetica, sans-serif; margin: 24px; color: #111; }
h1, h2, h3 { margin: 0 0 12px 0; }
.meta { color: #555; margin-bottom: 16px; }
.controls { margin-bottom: 18px; }
.controls a, .controls button { display: inline-block; margin-right: 8px; margin-bottom: 8px; padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; background: #f7f7f7; color: #111; text-decoration: none; cursor: pointer; }
.controls a:hover, .controls button:hover { background: #eee; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th, td { border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
.group { page-break-inside: avoid; margin: 0 0 20px 0; padding: 0 0 12px 0; border-bottom: 1px solid #ddd; }
.members { font-size: 1.05rem; }
.small { color: #666; font-size: 0.92rem; }
ul.project-links { columns: 2; -webkit-columns: 2; -moz-columns: 2; padding-left: 18px; }
ul.project-links li { margin-bottom: 6px; }
@media print {
  .controls { display: none !important; }
  body { margin: 12mm; }
  a { color: #111; text-decoration: none; }
}
"""

def _print_shell_html(title: str, body_html: str, auto_print: bool = False) -> bytes:
    auto_js = '<script>window.addEventListener("load", function(){ window.print(); });</script>' if auto_print else ''
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>{_print_styles_css()}</style>
  {auto_js}
</head>
<body>
{body_html}
</body>
</html>"""
    return doc.encode('utf-8')

def _build_detected_list_print_home(summary: dict, auto_print: bool = False) -> bytes:
    items = summary.get('items') or []
    links = []
    for item in items:
        gk = str(item.get('group_key') or '').strip()
        label = str(item.get('label') or gk)
        if not gk:
            continue
        href = f"/print/project/{quote(gk, safe='')}"
        links.append(f'<li><a href="{href}">{html.escape(label)}</a></li>')
    body = f"""
<h1>QR Inventory Print</h1>
<div class="meta">Active groups: {int(summary.get('count') or 0)}</div>
<div class="controls">
  <a href="/print/all">Open printable list</a>
  <a href="/print/all?autoprint=1">Print all now</a>
</div>
<h2>Projects</h2>
<ul class="project-links">{''.join(links) if links else '<li>No active groups</li>'}</ul>
"""
    return _print_shell_html('QR Inventory Print', body, auto_print=auto_print)

def _build_detected_list_print_all(summary: dict, auto_print: bool = False) -> bytes:
    items = summary.get('items') or []
    rows = []
    for item in items:
        label = html.escape(str(item.get('label') or item.get('group_key') or ''))
        members = ', '.join([str(x) for x in (item.get('members') or [])])
        locations = ', '.join([str(x) for x in (item.get('locations') or [])])
        rows.append(f"<tr><td>{label}</td><td>{html.escape(members)}</td><td class=\"small\">{html.escape(locations)}</td></tr>")
    body = f"""
<h1>QR Inventory</h1>
<div class=\"meta\">Detected groups: {int(summary.get('count') or 0)} | sort: {html.escape(str(summary.get('sort_order') or 'asc'))}</div>
<div class=\"controls\">
  <a href=\"/print\">Back</a>
  <button onclick=\"window.print()\">Print</button>
</div>
<table>
  <thead><tr><th>Project</th><th>Members</th><th>Locations</th></tr></thead>
  <tbody>{''.join(rows) if rows else '<tr><td colspan="3">No active groups</td></tr>'}</tbody>
</table>
"""
    return _print_shell_html('QR Inventory - Print All', body, auto_print=auto_print)

def _build_detected_list_print_group(summary: dict, group_key: str, auto_print: bool = False) -> bytes:
    target = None
    for item in (summary.get('items') or []):
        if str(item.get('group_key') or '') == str(group_key or ''):
            target = item
            break
    if target is None:
        body = f"""
<h1>QR Inventory</h1>
<div class=\"controls\">
  <a href=\"/print\">Back</a>
</div>
<p>Project not found: <b>{html.escape(str(group_key or ''))}</b></p>
"""
        return _print_shell_html('QR Inventory - Project not found', body, auto_print=False)

    label = str(target.get('label') or target.get('group_key') or '')
    members = target.get('members') or []
    locations = target.get('locations') or []
    payloads = target.get('payloads') or []
    member_items = ''.join([f'<li>{html.escape(str(m))}</li>' for m in members]) or '<li>-</li>'
    payload_items = ''.join([f'<li>{html.escape(str(p))}</li>' for p in payloads]) or '<li>-</li>'
    location_items = ''.join([f'<li>{html.escape(str(loc))}</li>' for loc in locations]) or '<li>-</li>'
    body = f"""
<h1>QR Inventory</h1>
<h2>{html.escape(label)}</h2>
<div class=\"meta\">Group key: {html.escape(str(target.get('group_key') or ''))} | members: {len(members)}</div>
<div class=\"controls\">
  <a href=\"/print\">Back</a>
  <a href=\"/print/all\">All projects</a>
  <button onclick=\"window.print()\">Print</button>
</div>
<div class=\"group\">
  <h3>Children / Members</h3>
  <ul class=\"members\">{member_items}</ul>
</div>
<div class=\"group\">
  <h3>Locations</h3>
  <ul>{location_items}</ul>
</div>
<div class=\"group\">
  <h3>Payload variants</h3>
  <ul>{payload_items}</ul>
</div>
"""
    return _print_shell_html(f'QR Inventory - {label}', body, auto_print=auto_print)

# ------------------------------------------------------------
# Multi-camera config parsing
# ------------------------------------------------------------
def _parse_zones(zones_raw):
    """
    Accepts:
      - list of {zone: "A1", rect_px: [x1,y1,x2,y2]}
      - dict of {"A1": [x1,y1,x2,y2], ...}
      - legacy dict of {"Z01": [..], ...}
    Returns dict: zone_name -> [x1,y1,x2,y2] (ints)
    """
    out = {}
    if zones_raw is None:
        return out

    if isinstance(zones_raw, str):
        # allow JSON string (legacy)
        try:
            zones_raw = json.loads(zones_raw) if zones_raw.strip() else {}
        except Exception:
            return out

    if isinstance(zones_raw, dict):
        for k, v in zones_raw.items():
            try:
                a = [int(v[0]), int(v[1]), int(v[2]), int(v[3])]
                out[str(k)] = a
            except Exception:
                continue
        return out

    if isinstance(zones_raw, list):
        for item in zones_raw:
            if isinstance(item, dict):
                z = item.get("zone") or item.get("id") or item.get("name")
                rect = item.get("rect_px") or item.get("rect") or item.get("box")
            else:
                z = None
                rect = item
            if not z:
                continue
            try:
                a = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
            except Exception:
                continue
            out[str(z)] = a
        return out

    return out

def _parse_legacy_zones():
    zones_raw = opts.get('zones', {})
    if isinstance(zones_raw, str):
        try:
            zones_dict = json.loads(zones_raw) if zones_raw.strip() else {}
        except Exception:
            zones_dict = {}
    elif isinstance(zones_raw, dict):
        zones_dict = zones_raw
    else:
        zones_dict = {}
    return _parse_zones(zones_dict)

def _parse_cameras():
    cams = opts.get("cameras")
    out = {}

    def _conf_has_url(conf):
        if not isinstance(conf, dict):
            return False
        url = _deep_get(conf, "stream", "rtsp_url", default=None) or conf.get("rtsp_url")
        return isinstance(url, str) and bool(url.strip())

    # New format: cameras is a dict keyed by camera id
    if isinstance(cams, dict) and cams:
        for cam_id, conf in cams.items():
            if not isinstance(conf, dict):
                continue
            out[str(cam_id)] = conf
        # treat as "not configured" if no camera has a URL (so legacy rtsp_url can still work)
        if any(_conf_has_url(c) for c in out.values()):
            return out
        out = {}

    # Alternate format: list of cameras with explicit id
    if isinstance(cams, list) and cams:
        for conf in cams:
            if not isinstance(conf, dict):
                continue
            cam_id = conf.get("id")
            if not cam_id:
                continue
            out[str(cam_id)] = conf
        if any(_conf_has_url(c) for c in out.values()):
            return out
        out = {}
    return out

_CAMERAS_RAW = _parse_cameras()

def _build_camera_runtime(cam_id: str, conf: dict):
    name = str(conf.get("name") or cam_id)

    stream = conf.get("stream") if isinstance(conf.get("stream"), dict) else {}
    url = stream.get("rtsp_url") or conf.get("rtsp_url") or ""
    url = str(url).strip()

    tls_verify = _parse_bool(stream.get("tls_verify", conf.get("tls_verify", TLS_VERIFY_DEFAULT)), TLS_VERIFY_DEFAULT)

    settings = conf.get("settings") if isinstance(conf.get("settings"), dict) else {}
    enabled = _parse_bool(settings.get("enabled", conf.get("enabled", True)), True)

    interval_s = _parse_int(settings.get("interval_s", conf.get("interval_s", DEFAULT_INTERVAL_S)), DEFAULT_INTERVAL_S)
    required_n = _parse_int(settings.get("required", conf.get("required", DEFAULT_REQUIRED)), DEFAULT_REQUIRED)
    restrict = _parse_bool(settings.get("restrict_to_zones", conf.get("restrict_to_zones", DEFAULT_RESTRICT_TO_ZONES)), DEFAULT_RESTRICT_TO_ZONES)

    # Stream info logging interval: can be global only for now
    stream_info_interval_minutes = _parse_int(opts.get("stream_info_interval_minutes", 0), 0)

    # Zones: accept either conf.zones (list/dict) or legacy dict already stored
    zones_dict = _parse_zones(conf.get("zones"))
    if not zones_dict and isinstance(conf.get("zones"), dict):
        zones_dict = _parse_zones(conf.get("zones"))
    if not zones_dict and "zones" in conf and isinstance(conf["zones"], dict):
        zones_dict = _parse_zones(conf["zones"])

    # If user provided legacy dict in cam1.zones (from _parse_cameras), it might already be dict of zone->rect
    if not zones_dict and isinstance(conf.get("zones"), dict):
        zones_dict = conf.get("zones")

    return {
        "id": cam_id,
        "name": name,
        "url": url,
        "tls_verify": tls_verify,
        "enabled": enabled,
        "interval_s": max(1, interval_s),
        "required": max(1, required_n),
        "restrict_to_zones": bool(restrict),
        "zones": zones_dict if isinstance(zones_dict, dict) else {},
        "stream_info_interval_minutes": max(0, stream_info_interval_minutes),
    }

CAMERAS = {}
for _cid, _conf in _CAMERAS_RAW.items():
    try:
        cam_rt = _build_camera_runtime(str(_cid), _conf if isinstance(_conf, dict) else {})
        CAMERAS[cam_rt["id"]] = cam_rt
    except Exception as e:
        logger.exception("Failed parsing camera config %s: %s", _cid, e)

CAMERA_IDS = sorted(CAMERAS.keys())
PRIMARY_CAMERA_ID = CAMERA_IDS[0] if CAMERA_IDS else None

if not CAMERA_IDS:
    logger.error("No cameras configured. Please set 'cameras:' in the add-on options.")

# ------------------------------------------------------------
# Persistence (inventory mapping)
# ------------------------------------------------------------
INV_LOCK = threading.Lock()

confirmed = {}
inv_path = '/data/inventory.json'
if os.path.exists(inv_path):
    try:
        with open(inv_path, 'r', encoding='utf-8') as f:
            confirmed = json.load(f) or {}
    except Exception:
        confirmed = {}

def _atomic_write_json(path: str, obj):
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.exception("Failed writing %s: %s", path, e)
        return False

def persist_mapping(payload: str, location: str):
    """
    Persist payload -> location mapping.
    location is typically "<camera_id>.<zone>", e.g. "cam1.A1".
    """
    payload = (payload or "").strip()
    location = (location or "").strip()
    if not payload or not location:
        return

    with INV_LOCK:
        prev = confirmed.get(payload)
        if prev == location:
            return
        confirmed[payload] = location

    if _atomic_write_json(inv_path, confirmed):
        logger.info('Persisted mapping %s -> %s', payload, location)

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
def _run_ffprobe(url: str, tls_verify: bool):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-select_streams", "v:0"]
    if url.lower().startswith("rtsps://") and (not bool(tls_verify)):
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

def _log_stream_info(tag: str, url: str, tls_verify: bool):
    if not url:
        return
    info = _run_ffprobe(url, tls_verify)
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
def get_frame_ffmpeg(url: str, tls_verify: bool):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp"]
    if url.lower().startswith("rtsps://") and (not bool(tls_verify)):
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
# key: "<cam_id>:<zone>"
DEBUG_STATE = {}
# Track latest debug entry (global + per camera)
DEBUG_LATEST = {"key": None, "ts": 0, "by_cam": {}}

def _set_debug(cam_id: str, zone: str, debug_json: dict, roi: np.ndarray,
               roi_best: np.ndarray | None, marked_roi: np.ndarray | None, marked_best: np.ndarray | None):
    if not zone or (not _debug_enabled_for_zone(zone)):
        return
    cam_id = str(cam_id or "").strip() or "cam"
    zone = str(zone).strip()
    key = f"{cam_id}:{zone}"
    ts = int(time.time())
    entry = {
        "camera": cam_id,
        "zone": zone,
        "ts": ts,
        "debug": debug_json,
        "roi_png": _encode_png(roi) if roi is not None else None,
        "roi_best_png": _encode_png(roi_best) if roi_best is not None else None,
        "roi_marked_png": _encode_png(marked_roi) if marked_roi is not None else None,
        "roi_best_marked_png": _encode_png(marked_best) if marked_best is not None else None,
    }

    with DEBUG_LOCK:
        DEBUG_STATE[key] = entry
        # global latest
        if ts >= int(DEBUG_LATEST.get("ts") or 0):
            DEBUG_LATEST["ts"] = ts
            DEBUG_LATEST["key"] = key
        # per-camera latest
        by_cam = DEBUG_LATEST.get("by_cam") or {}
        cur = by_cam.get(cam_id) or {}
        if ts >= int(cur.get("ts") or 0):
            by_cam[cam_id] = {"ts": ts, "key": key}
        DEBUG_LATEST["by_cam"] = by_cam

# ------------------------------------------------------------
# Zone scan
# ------------------------------------------------------------
def scan_zone(frame_gray: np.ndarray, cam_id: str, zname: str, box, pad_px: int, scales: list[float]):
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
        "camera": str(cam_id),
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
                if _debug_enabled_for_zone(zname) and best_pre is None:
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
                _set_debug(cam_id, zname, dbg, roi, best_pre, marked_roi, marked_best)
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
                        _set_debug(cam_id, zname, dbg, roi, best_pre, marked_roi, marked_best)
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
    _set_debug(cam_id, zname, dbg, roi, best_pre, marked_roi, marked_best)
    return None, miss

# ------------------------------------------------------------
# Detection entrypoint
# ------------------------------------------------------------
def detect_qr(frame_bgr: np.ndarray, cam_id: str, zones_dict: dict, restrict_to_zones_flag: bool):
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    zones_ok = isinstance(zones_dict, dict) and bool(zones_dict)

    if restrict_to_zones_flag and not zones_ok:
        return [], True

    detections = []
    if zones_ok:
        pad = max(0, int(roi_padding_px))
        scales = _dedupe_sorted(ROI_SCALES + ZONE_EXTRA_SCALES) or [2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
        for zname, box in zones_dict.items():
            dec, miss = scan_zone(frame_gray, str(cam_id), str(zname), box, pad, scales)
            if dec is not None:
                detections.append(dec)
            elif miss is not None:
                detections.append(miss)
        return detections, True if restrict_to_zones_flag else False

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
def _draw_alignment_helper_lines(img: np.ndarray):
    if img is None or img.size == 0 or not overlay_alignment_enabled:
        return img

    h, w = img.shape[:2]
    if h <= 0 or w <= 0:
        return img

    color = OVERLAY_ALIGNMENT_COLOR_BGR
    thickness = max(1, int(overlay_alignment_width))
    cx = int(round((w - 1) / 2.0))
    cy = int(round((h - 1) / 2.0))

    if overlay_alignment_direction in ("horizontal", "both"):
        cv2.line(img, (0, cy), (w - 1, cy), color, thickness, cv2.LINE_AA)
    if overlay_alignment_direction in ("vertical", "both"):
        cv2.line(img, (cx, 0), (cx, h - 1), color, thickness, cv2.LINE_AA)

    return img


def _draw_margin_helper_box(img: np.ndarray):
    if img is None or img.size == 0 or not overlay_margin_enabled:
        return img

    h, w = img.shape[:2]
    if h <= 1 or w <= 1:
        return img

    color = OVERLAY_ALIGNMENT_COLOR_BGR
    thickness = max(1, int(overlay_alignment_width))
    max_margin = max(0, min((w - 2) // 2, (h - 2) // 2))
    margin = max(0, min(int(overlay_margin_px), max_margin))
    x1, y1 = margin, margin
    x2, y2 = max(x1, w - 1 - margin), max(y1, h - 1 - margin)
    if x2 <= x1 or y2 <= y1:
        return img

    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
    return img


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

    _draw_margin_helper_box(out)
    _draw_alignment_helper_lines(out)
    return out
# ------------------------------------------------------------
# MQTT publishing
# ------------------------------------------------------------
def _zone_status_to_mqtt_payload(cam_id: str, cam_name: str, zone_name: str, zone_state: dict | None):
    zone_state = zone_state if isinstance(zone_state, dict) else {"kind": "none", "det": None}
    kind = str(zone_state.get("kind") or "none")
    det = zone_state.get("det") if isinstance(zone_state.get("det"), dict) else None

    if kind == "decoded" and det and det.get("payload") is not None:
        state = str(det.get("payload"))
        status = "decoded"
        decoded = True
    elif kind == "candidate" and det and (det.get("reason") == "detected_unresolved" or (det.get("points") and not det.get("no_quad"))):
        state = MQTT_STATE_DETECTED_NO_VALUE
        status = MQTT_STATE_DETECTED_NO_VALUE
        decoded = False
    else:
        det = None
        state = MQTT_STATE_NONE
        status = MQTT_STATE_NONE
        decoded = False

    payload = {
        "state": state,
        "attributes": {
            "camera_id": str(cam_id),
            "camera_name": str(cam_name),
            "zone": str(zone_name),
            "sensor_name": _mqtt_sensor_name(cam_name, zone_name),
            "status": status,
            "decoded": bool(decoded),
            "qr_code": (str(det.get("payload")) if decoded and det is not None and det.get("payload") is not None else None),
            "reason": (det.get("reason") if det else None),
            "score": (float(det.get("score")) if det and det.get("score") is not None else None),
            "centroid": (det.get("centroid") if det else None),
            "points": (det.get("points") if det else None),
            "edge_px": (float((det.get("diag") or {}).get("edge_px")) if det and (det.get("diag") or {}).get("edge_px") is not None else None),
            "source": ((det.get("diag") or {}).get("src") if det else None),
        },
    }
    return payload

class MQTTManager:
    def __init__(self, cameras: dict):
        self.cameras = cameras if isinstance(cameras, dict) else {}
        self.enabled = bool(MQTT_ENABLED and MQTT_HOST)
        self.client = None
        self.connected = False
        self.lock = threading.Lock()
        self.zone_cache = {}
        self.discovery_payloads = {}
        self.detected_list_cache = build_detected_list_summary({})
        self._build_zone_cache()

    def _build_zone_cache(self):
        for cam_id, cam in self.cameras.items():
            cam_name = str((cam or {}).get("name") or cam_id)
            zones = (cam or {}).get("zones") or {}
            if not isinstance(zones, dict):
                continue
            for zone_name in zones.keys():
                key = self._sensor_key(cam_id, zone_name)
                payload = _zone_status_to_mqtt_payload(cam_id, cam_name, str(zone_name), {"kind": "none", "det": None})
                self.zone_cache[key] = payload
                self.discovery_payloads[key] = self._discovery_payload(cam_id, cam_name, str(zone_name))

    def _sensor_key(self, cam_id: str, zone_name: str) -> str:
        return f"{str(cam_id)}::{str(zone_name)}"

    def _node_id(self) -> str:
        return _slugify_token(MQTT_CLIENT_ID, default="qr_inventory", lower=True)

    def _entity_token(self, cam_name: str, zone_name: str) -> str:
        return _slugify_token(_mqtt_sensor_name(cam_name, zone_name), default="zone", lower=True)

    def _unique_id(self, cam_id: str, zone_name: str) -> str:
        return f"qr_inventory_{_slugify_token(cam_id, default='cam', lower=True)}_{_slugify_token(zone_name, default='zone', lower=True)}"

    def _state_topic(self, cam_id: str, zone_name: str) -> str:
        return f"{MQTT_TOPIC_PREFIX}/zones/{_slugify_token(cam_id, default='cam')}/{_slugify_token(zone_name, default='zone')}/state"

    def _attributes_topic(self, cam_id: str, zone_name: str) -> str:
        return f"{MQTT_TOPIC_PREFIX}/zones/{_slugify_token(cam_id, default='cam')}/{_slugify_token(zone_name, default='zone')}/attributes"

    def _discovery_topic(self, cam_name: str, zone_name: str) -> str:
        return f"{MQTT_DISCOVERY_PREFIX}/sensor/{self._node_id()}/{self._entity_token(cam_name, zone_name)}/config"

    def _device_payload(self, cam_id: str, cam_name: str) -> dict:
        return {
            "identifiers": [f"qr_inventory_{_slugify_token(cam_id, default='cam', lower=True)}"],
            "name": str(cam_name),
            "manufacturer": "QR Inventory",
            "model": "QR Zone Scanner",
            "sw_version": "0.6.2.0",
        }

    def _discovery_payload(self, cam_id: str, cam_name: str, zone_name: str) -> dict:
        sensor_name = _mqtt_sensor_name(cam_name, zone_name)
        entity_token = self._entity_token(cam_name, zone_name)
        return {
            "name": sensor_name,
            "unique_id": self._unique_id(cam_id, zone_name),
            "default_entity_id": f"sensor.{entity_token}",
            "state_topic": self._state_topic(cam_id, zone_name),
            "json_attributes_topic": self._attributes_topic(cam_id, zone_name),
            "availability_topic": MQTT_AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": "mdi:qrcode-scan",
            "device": self._device_payload(cam_id, cam_name),
        }

    def _detected_list_discovery_topic(self) -> str:
        return f"{MQTT_DISCOVERY_PREFIX}/sensor/{self._node_id()}/detected_list/config"

    def _detected_list_state_topic(self) -> str:
        return f"{MQTT_TOPIC_PREFIX}/detected_list/state"

    def _detected_list_attributes_topic(self) -> str:
        return f"{MQTT_TOPIC_PREFIX}/detected_list/attributes"

    def _detected_list_discovery_payload(self) -> dict:
        return {
            "name": "qr_inventory_detected_list",
            "unique_id": f"{self._node_id()}_detected_list",
            "default_entity_id": "sensor.qr_inventory_detected_list",
            "state_topic": self._detected_list_state_topic(),
            "json_attributes_topic": self._detected_list_attributes_topic(),
            "availability_topic": MQTT_AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": "mdi:format-list-bulleted",
            "device": {
                "identifiers": [f"{self._node_id()}_summary"],
                "name": "QR Inventory Summary",
                "manufacturer": "QR Inventory",
                "model": "Detected List",
                "sw_version": "0.6.4.0",
            },
        }

    def _publish_detected_list_sensor(self, summary: dict):
        if self.client is None or not self.connected or not overlay_detected_list_enabled:
            return
        attrs = {
            "count": int(summary.get("count") or 0),
            "regex": summary.get("regex"),
            "items": summary.get("items") or [],
            "lines": summary.get("lines") or [],
            "text": summary.get("text") or "",
            "ts": int(summary.get("ts") or time.time()),
        }
        try:
            self.client.publish(
                self._detected_list_discovery_topic(),
                payload=json.dumps(self._detected_list_discovery_payload(), ensure_ascii=False),
                qos=MQTT_QOS,
                retain=True,
            )
            self.client.publish(
                self._detected_list_attributes_topic(),
                payload=json.dumps(attrs, ensure_ascii=False),
                qos=MQTT_QOS,
                retain=False,
            )
            self.client.publish(
                self._detected_list_state_topic(),
                payload=str(int(summary.get("count") or 0)),
                qos=MQTT_QOS,
                retain=False,
            )
        except Exception:
            logger.exception("MQTT publish failed for detected list")

    def start(self):
        if not MQTT_ENABLED:
            logger.info("MQTT disabled")
            return
        if not MQTT_HOST:
            logger.error("MQTT enabled but mqtt.host is empty")
            return
        if not _MQTT_OK:
            logger.error("MQTT enabled but paho-mqtt is not installed")
            return

        cb_api = None
        if CallbackAPIVersion is not None:
            cb_api = getattr(CallbackAPIVersion, "API_VERSION2", None)
            if cb_api is None:
                cb_api = getattr(CallbackAPIVersion, "VERSION2", None)
            if cb_api is None:
                cb_api = getattr(CallbackAPIVersion, "VERSION1", None)
        try:
            if cb_api is not None:
                self.client = mqtt.Client(callback_api_version=cb_api, client_id=MQTT_CLIENT_ID)
            else:
                self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        except Exception:
            self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, None if MQTT_PASSWORD in (None, "") else str(MQTT_PASSWORD))
        self.client.enable_logger(logger)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_log = self._on_log
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.will_set(MQTT_AVAILABILITY_TOPIC, payload="offline", qos=MQTT_QOS, retain=True)
        logger.info("MQTT enabled: broker=%s:%s topic_prefix=%s discovery_prefix=%s", MQTT_HOST, MQTT_PORT, MQTT_TOPIC_PREFIX, MQTT_DISCOVERY_PREFIX)
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        except Exception as e:
            logger.error("MQTT initial connect failed to %s:%s: %s", MQTT_HOST, MQTT_PORT, e)
            return
        self.client.loop_start()

    def stop(self):
        try:
            if self.client is not None:
                self.client.publish(MQTT_AVAILABILITY_TOPIC, payload="offline", qos=MQTT_QOS, retain=True)
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = True
        logger.info("MQTT connected: %s", reason_code)
        try:
            client.subscribe(MQTT_HA_BIRTH_TOPIC, qos=MQTT_QOS)
        except Exception:
            logger.exception("MQTT subscribe failed for %s", MQTT_HA_BIRTH_TOPIC)
        self._publish_availability("online")
        self.republish_all()

    def _on_connect_fail(self, client, userdata):
        self.connected = False
        logger.error("MQTT connect failed to %s:%s", MQTT_HOST, MQTT_PORT)

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        self.connected = False
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_log(self, client, userdata, level, buf):
        try:
            msg = str(buf or "").strip()
        except Exception:
            msg = ""
        if not msg:
            return
        low = msg.lower()
        if ("failed" in low) or ("refused" in low) or ("error" in low) or ("unreachable" in low):
            logger.warning("MQTT log: %s", msg)

    def _on_message(self, client, userdata, msg):
        try:
            payload = (msg.payload.decode("utf-8", errors="ignore") or "").strip().lower()
        except Exception:
            payload = ""
        if msg.topic == MQTT_HA_BIRTH_TOPIC and payload == "online":
            logger.info("MQTT birth message received on %s; republishing discovery and states", MQTT_HA_BIRTH_TOPIC)
            self.republish_all()

    def _publish_availability(self, state: str):
        if self.client is None:
            return
        try:
            self.client.publish(MQTT_AVAILABILITY_TOPIC, payload=str(state), qos=MQTT_QOS, retain=True)
        except Exception:
            logger.exception("MQTT availability publish failed")

    def _publish_sensor(self, cam_id: str, cam_name: str, zone_name: str, payload: dict):
        if self.client is None or not self.connected:
            return
        key = self._sensor_key(cam_id, zone_name)
        discovery_payload = self.discovery_payloads.get(key) or self._discovery_payload(cam_id, cam_name, zone_name)
        try:
            self.client.publish(
                self._discovery_topic(cam_name, zone_name),
                payload=json.dumps(discovery_payload, ensure_ascii=False),
                qos=MQTT_QOS,
                retain=True,
            )
            self.client.publish(
                self._attributes_topic(cam_id, zone_name),
                payload=json.dumps(payload.get("attributes") or {}, ensure_ascii=False),
                qos=MQTT_QOS,
                retain=MQTT_RETAIN,
            )
            self.client.publish(
                self._state_topic(cam_id, zone_name),
                payload=str(payload.get("state") or MQTT_STATE_NONE),
                qos=MQTT_QOS,
                retain=MQTT_RETAIN,
            )
        except Exception:
            logger.exception("MQTT publish failed for camera=%s zone=%s", cam_id, zone_name)

    def republish_all(self):
        if self.client is None or not self.connected:
            return
        with self.lock:
            items = list(self.zone_cache.items())
            detected_list_cache = dict(self.detected_list_cache or {})
        for key, payload in items:
            cam_id, zone_name = key.split("::", 1)
            cam = self.cameras.get(cam_id) or {}
            cam_name = str(cam.get("name") or cam_id)
            self._publish_sensor(cam_id, cam_name, zone_name, payload)
        self._publish_detected_list_sensor(detected_list_cache)

    def publish_camera_zone_states(self, cam_id: str, cam_name: str, zones_dict: dict, zone_status: dict):
        if not self.enabled or not isinstance(zones_dict, dict):
            return
        for zone_name in zones_dict.keys():
            zone_state = zone_status.get(zone_name) if isinstance(zone_status, dict) else None
            payload = _zone_status_to_mqtt_payload(cam_id, cam_name, str(zone_name), zone_state)
            key = self._sensor_key(cam_id, str(zone_name))
            with self.lock:
                self.zone_cache[key] = payload
                self.discovery_payloads[key] = self._discovery_payload(cam_id, cam_name, str(zone_name))
            self._publish_sensor(cam_id, cam_name, str(zone_name), payload)

    def publish_detected_list(self, states: dict):
        if not self.enabled or not overlay_detected_list_enabled:
            return
        summary = build_detected_list_summary(states if isinstance(states, dict) else {})
        with self.lock:
            self.detected_list_cache = summary
        self._publish_detected_list_sensor(summary)

MQTT_MANAGER = MQTTManager(CAMERAS)

# ------------------------------------------------------------
# HTTP server state + handler
# ------------------------------------------------------------
STATE_LOCK = threading.Lock()
# cam_id -> {"ts": int, "frame_png": bytes|None, "overlay_png": bytes|None, "detections": list, "frame_info": dict}
STATE = {}

def _get_cam_state(cam_id: str):
    with STATE_LOCK:
        return (STATE.get(cam_id) or {}).copy()

def _get_all_states():
    with STATE_LOCK:
        return {k: (v or {}).copy() for k, v in STATE.items()}

def _send_json(handler: BaseHTTPRequestHandler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except BrokenPipeError:
        return

class OverlayHandler(BaseHTTPRequestHandler):
    def _send(self, code, content_type, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            return

    def do_GET(self):
        path = unquote(urlparse(self.path).path or "/")

        # Index
        if path in ("/", "/index.html"):
            cams_html = ""
            for cid in CAMERA_IDS:
                cam = CAMERAS.get(cid) or {}
                cname = cam.get("name") or cid
                cams_html += f'<li><b>{cid}</b> ({cname}) - ' \
                            f'<a href="/{cid}/overlay.png">overlay</a> | ' \
                            f'<a href="/{cid}/frame.png">frame</a> | ' \
                            f'<a href="/{cid}/detections.json">detections</a></li>\n'
            dz = "*" if DEBUG_ALL_ZONES else ", ".join(sorted(DEBUG_ZONES)) if DEBUG_ZONES else "-"
            html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>QR Inventory</title></head>
<body style="font-family: sans-serif">
  <h3>QR Inventory</h3>
  <p>Cameras:</p>
  <ul>{cams_html}</ul>
  <p>Aggregate: <a href="/detections.json">/detections.json</a></p>
  <p>Detected list: <a href="/detected-list.json">/detected-list.json</a></p>
  <p>Print view: <a href="/print">/print</a> | <a href="/print/all">/print/all</a></p>
  <p>Debug zones: <code>{dz}</code></p>
  <p>Debug index: <a href="/debug/index.json">/debug/index.json</a></p>
</body></html>""".encode("utf-8")
            return self._send(200, "text/html; charset=utf-8", html)

        # Aggregate detections
        if path == "/detections.json":
            states = _get_all_states()
            cams = {}
            for cid, st in states.items():
                cams[cid] = {
                    "ts": st.get("ts") or 0,
                    "detections": st.get("detections") or [],
                    "frame_info": st.get("frame_info") or {},
                    "camera": {"id": cid, "name": (CAMERAS.get(cid) or {}).get("name") or cid},
                }
            return _send_json(self, {"ts": int(time.time()), "cameras": cams})

        if path == "/detected-list.json":
            return _send_json(self, build_detected_list_summary(_get_all_states()))

        if path.startswith('/print'):
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query or '')
            auto_print = str((q.get('autoprint') or ['0'])[0]).strip().lower() in ('1', 'true', 'yes', 'on')
            summary = build_detected_list_summary(_get_all_states())
            clean_path = unquote(parsed.path or '/print')
            if clean_path in ('/print', '/print/', '/print/index.html'):
                return self._send(200, 'text/html; charset=utf-8', _build_detected_list_print_home(summary, auto_print=auto_print))
            if clean_path in ('/print/all', '/print/all.html'):
                return self._send(200, 'text/html; charset=utf-8', _build_detected_list_print_all(summary, auto_print=auto_print))
            if clean_path.startswith('/print/project/'):
                gk = clean_path[len('/print/project/'):].strip('/')
                if gk.endswith('.html'):
                    gk = gk[:-5]
                return self._send(200, 'text/html; charset=utf-8', _build_detected_list_print_group(summary, gk, auto_print=auto_print))

        # Legacy single-camera endpoints
        if PRIMARY_CAMERA_ID:
            if path in ("/overlay.png", f"/{OVERLAY_PNG_NAME}"):
                st = _get_cam_state(PRIMARY_CAMERA_ID)
                b = st.get("overlay_png")
                if not b:
                    return self._send(503, "text/plain; charset=utf-8", b"overlay not ready")
                return self._send(200, "image/png", b)

            if path == "/frame.png":
                st = _get_cam_state(PRIMARY_CAMERA_ID)
                b = st.get("frame_png")
                if not b:
                    return self._send(503, "text/plain; charset=utf-8", b"frame not ready")
                return self._send(200, "image/png", b)

        # Per-camera endpoints: /<cam_id>/overlay.png | frame.png | detections.json
        parts = [p for p in path.strip("/").split("/") if p]
        if parts:
            cid = parts[0]
            if cid in CAMERAS:
                st = _get_cam_state(cid)
                if len(parts) == 2 and parts[1] == "overlay.png":
                    b = st.get("overlay_png")
                    if not b:
                        return self._send(503, "text/plain; charset=utf-8", b"overlay not ready")
                    return self._send(200, "image/png", b)
                if len(parts) == 2 and parts[1] == "frame.png":
                    b = st.get("frame_png")
                    if not b:
                        return self._send(503, "text/plain; charset=utf-8", b"frame not ready")
                    return self._send(200, "image/png", b)
                if len(parts) == 2 and parts[1] == "detections.json":
                    return _send_json(self, {
                        "ts": st.get("ts") or 0,
                        "camera": {"id": cid, "name": (CAMERAS.get(cid) or {}).get("name") or cid},
                        "detections": st.get("detections") or [],
                        "frame_info": st.get("frame_info") or {},
                    })

        # Short overlay route: /overlays/<cam_id>.png
        if path.startswith(OVERLAY_ROUTE_PREFIX + "/"):
            cid = path[len(OVERLAY_ROUTE_PREFIX) + 1:].strip("/")
            if cid.endswith(".png"):
                cid = cid[:-4]
            if cid in CAMERAS:
                st = _get_cam_state(cid)
                b = st.get("overlay_png")
                if not b:
                    return self._send(503, "text/plain; charset=utf-8", b"overlay not ready")
                return self._send(200, "image/png", b)

        # Short frame route: /frames/<cam_id>.png
        if path.startswith(FRAME_ROUTE_PREFIX + "/"):
            cid = path[len(FRAME_ROUTE_PREFIX) + 1:].strip("/")
            if cid.endswith(".png"):
                cid = cid[:-4]
            if cid in CAMERAS:
                st = _get_cam_state(cid)
                b = st.get("frame_png")
                if not b:
                    return self._send(503, "text/plain; charset=utf-8", b"frame not ready")
                return self._send(200, "image/png", b)

        # Debug index + per-zone debug blobs
        if path == "/debug/index.json":
            with DEBUG_LOCK:
                keys = sorted(DEBUG_STATE.keys())
                latest = DEBUG_LATEST.copy()
            # Provide keys like "cam1:A1"
            return _send_json(self, {"keys": keys, "latest": latest})

        if path.startswith("/debug/"):
            parts = [p for p in path.strip("/").split("/") if p]
            # /debug/<cam_id>/<zone>/debug.json
            if len(parts) >= 4:
                _, cid, zone, tail = parts[0], parts[1], parts[2], "/".join(parts[3:])
                key = f"{cid}:{zone}"
                with DEBUG_LOCK:
                    entry = DEBUG_STATE.get(key)
                if not entry:
                    return self._send(404, "text/plain; charset=utf-8", b"no debug available")
                if tail == "debug.json":
                    return _send_json(self, {"camera": cid, "zone": zone, "ts": entry.get("ts") or 0, "debug": entry.get("debug")})
                if tail == "roi.png":
                    b = entry.get("roi_png")
                elif tail == "roi_best.png":
                    b = entry.get("roi_best_png")
                elif tail == "roi_marked.png":
                    b = entry.get("roi_marked_png")
                elif tail == "roi_best_marked.png":
                    b = entry.get("roi_best_marked_png")
                else:
                    b = None
                if not b:
                    return self._send(404, "text/plain; charset=utf-8", b"no debug image available")
                return self._send(200, "image/png", b)

        return self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, fmt, *args):
        logger.debug("HTTP: " + fmt, *args)

def start_http_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", int(HTTP_PORT)), OverlayHandler)
    logger.info("Overlay HTTP server listening on :%d", int(HTTP_PORT))
    httpd.serve_forever()

# ------------------------------------------------------------
# Main loop (multi-camera)
# ------------------------------------------------------------
def _safe_cam_id(s: str) -> str:
    s = (s or "").strip()
    return s if s else "cam"

def _camera_location(cam_id: str, zone: str) -> str:
    return f"{_safe_cam_id(cam_id)}.{str(zone).strip()}"

def _write_camera_detections(cam_id: str, payload: dict):
    try:
        out_dir = f"/data/{_safe_cam_id(cam_id)}"
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "detections.json")
        _atomic_write_json(path, payload)
    except Exception:
        logger.exception("Failed writing detections.json for %s", cam_id)

class CameraWorker(threading.Thread):
    def __init__(self, cam_cfg: dict):
        super().__init__(daemon=True)
        self.cam = cam_cfg or {}
        self.cam_id = self.cam.get("id") or "cam"
        self.cam_name = self.cam.get("name") or self.cam_id
        self.url = self.cam.get("url") or ""
        self.tls_verify = bool(self.cam.get("tls_verify", TLS_VERIFY_DEFAULT))
        self.interval_s = int(self.cam.get("interval_s") or DEFAULT_INTERVAL_S)
        self.required = int(self.cam.get("required") or DEFAULT_REQUIRED)
        self.restrict_to_zones = bool(self.cam.get("restrict_to_zones", DEFAULT_RESTRICT_TO_ZONES))
        self.zones = self.cam.get("zones") or {}
        self.stream_info_interval_minutes = int(self.cam.get("stream_info_interval_minutes") or STREAM_INFO_INTERVAL_MINUTES_DEFAULT)
        self.last_stream_info_ts = 0.0
        self.history = defaultdict(lambda: deque(maxlen=max(1, self.required)))

    def run(self):
        if not self.url:
            logger.error("Camera %s has empty rtsp_url; skipping", self.cam_id)
            return

        logger.info(
            "Camera %s (%s) starting: url=%s interval=%ss required=%s zones=%s restrict_to_zones=%s",
            self.cam_id, self.cam_name, self.url, self.interval_s, self.required, len(self.zones), self.restrict_to_zones
        )

        _log_stream_info(f"{self.cam_id} STARTUP", self.url, self.tls_verify)

        cycle_idx = 0
        while True:
            cycle_idx += 1
            try:
                # Periodic stream info
                if self.stream_info_interval_minutes > 0:
                    now = time.time()
                    if self.last_stream_info_ts == 0 or (now - self.last_stream_info_ts) >= self.stream_info_interval_minutes * 60:
                        _log_stream_info(f"{self.cam_id} PERIODIC", self.url, self.tls_verify)
                        self.last_stream_info_ts = now

                frame = get_frame_ffmpeg(self.url, self.tls_verify)
                if frame is None:
                    time.sleep(self.interval_s)
                    continue

                detections, zone_only_active = detect_qr(frame, self.cam_id, self.zones, self.restrict_to_zones)
                zone_status = compute_zone_status(self.zones, detections)

                # stamp camera id onto detections
                for d in detections:
                    try:
                        d["camera"] = self.cam_id
                    except Exception:
                        pass

                decoded = [d for d in detections if d.get("decoded", False) and d.get("payload") and d.get("zone")]
                by_payload = defaultdict(list)
                for d in decoded:
                    by_payload[d["payload"]].append(d)

                for payload, items in by_payload.items():
                    # Resolve conflicts within this camera cycle
                    if len(items) > 1:
                        def _rank(it):
                            sc = float(it.get("score") or 0.0)
                            ep = float((it.get("diag") or {}).get("edge_px") or 0.0)
                            return (sc, ep)
                        best = sorted(items, key=_rank, reverse=True)[0]
                        logger.warning("Payload conflict resolved: cam=%s payload=%s choose=%s", self.cam_id, payload, best.get("zone"))
                        items = [best]

                    d = items[0]
                    zone = str(d.get("zone"))
                    loc = _camera_location(self.cam_id, zone)

                    self.history[payload].append(loc)
                    logger.info("Detected cam=%s payload=%s zone=%s history=%s", self.cam_id, payload, zone, list(self.history[payload]))

                    if len(self.history[payload]) >= max(1, self.required) and len(set(self.history[payload])) == 1:
                        persist_mapping(payload, loc)

                MQTT_MANAGER.publish_camera_zone_states(self.cam_id, self.cam_name, self.zones, zone_status)

                overlay = draw_overlay(frame, detections, self.zones)

                frame_png = _encode_png(frame)
                overlay_png = _encode_png(overlay)

                fi = {
                    "camera_id": self.cam_id,
                    "camera_name": self.cam_name,
                    "frame_w": int(frame.shape[1]),
                    "frame_h": int(frame.shape[0]),
                    "decoded": sum(1 for d in detections if d.get("decoded", False)),
                    "miss": sum(1 for d in detections if not d.get("decoded", False)),
                    "restrict_to_zones_active": bool(zone_only_active),
                    "debug_zones": ("*" if DEBUG_ALL_ZONES else sorted(DEBUG_ZONES)),
                    "cycle": int(cycle_idx),
                }

                ts = int(time.time())

                # Persist detections for this camera
                _write_camera_detections(self.cam_id, {
                    "ts": ts,
                    "camera": {"id": self.cam_id, "name": self.cam_name},
                    "detections": detections,
                    "frame_info": fi,
                })

                # Update in-memory HTTP state
                with STATE_LOCK:
                    STATE[self.cam_id] = {
                        "ts": ts,
                        "frame_png": frame_png,
                        "overlay_png": overlay_png,
                        "detections": detections,
                        "frame_info": fi,
                    }
                    states_snapshot = {k: (v or {}).copy() for k, v in STATE.items()}

                MQTT_MANAGER.publish_detected_list(states_snapshot)

            except Exception as e:
                logger.exception("Error in camera loop (%s): %s", self.cam_id, e)

            time.sleep(self.interval_s)

# Start MQTT
MQTT_MANAGER.start()

# Start HTTP server
threading.Thread(target=start_http_server, daemon=True).start()

# Start cameras
if CAMERA_IDS:
    for cid in CAMERA_IDS:
        cam = CAMERAS.get(cid) or {}
        if not bool(cam.get("enabled", True)):
            logger.info("Camera %s disabled; skipping", cid)
            continue
        CameraWorker(cam).start()

# Keep process alive
while True:
    time.sleep(3600)

