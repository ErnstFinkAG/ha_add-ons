#!/usr/bin/env python3
import json
import logging
import math
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, abort, jsonify, render_template, request

LOG = logging.getLogger("public_sensors")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

OPTIONS_PATH = Path("/data/options.json")
HA_API_BASE = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PUBLIC_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

APP = Flask(__name__, static_folder="static", template_folder="templates")
SESSION = requests.Session()
CACHE_LOCK = threading.Lock()
CACHE: dict[str, Any] = {
    "pages": [],
    "data": {},
    "errors": {},
    "updated_monotonic": {},
    "global_error": "Waiting for first Home Assistant history refresh",
}


def clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def load_raw_options() -> dict[str, Any]:
    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as handle:
            configured = json.load(handle)
            if isinstance(configured, dict):
                return configured
    except FileNotFoundError:
        LOG.warning("%s does not exist; using defaults", OPTIONS_PATH)
    except Exception:
        LOG.exception("Could not read %s; using defaults", OPTIONS_PATH)
    return {}


def normalize_page(raw: dict[str, Any], index: int) -> dict[str, Any]:
    public_path = str(
        raw.get("path") or raw.get("id") or f"sensor-{index + 1}"
    ).strip().lower()

    if not PUBLIC_PATH_RE.fullmatch(public_path):
        raise ValueError(
            f"Invalid public path '{public_path}'. "
            "Use lowercase letters, numbers, '-' or '_'."
        )

    sensor_entity = str(raw.get("sensor_entity") or "").strip()
    if not sensor_entity:
        raise ValueError(f"Page '{public_path}' has no sensor_entity")

    sensor_min = as_float(raw.get("sensor_min"), 0.0)
    sensor_max = as_float(raw.get("sensor_max"), 100.0)
    if sensor_max <= sensor_min:
        raise ValueError(
            f"Page '{public_path}' sensor_max must be greater than sensor_min"
        )

    return {
        "path": public_path,
        "enabled": bool(raw.get("enabled", True)),
        "title": str(raw.get("title") or public_path),
        "sensor_entity": sensor_entity,
        "sensor_name": str(raw.get("sensor_name") or "").strip(),
        "sensor_unit": str(raw.get("sensor_unit") or "").strip(),
        "decimals": clamp_int(raw.get("decimals"), 0, 6, 2),
        "sensor_min": sensor_min,
        "sensor_max": sensor_max,
        "binary_entity": str(raw.get("binary_entity") or "").strip(),
        "binary_name": str(raw.get("binary_name") or "Status").strip(),
        "history_hours": clamp_int(raw.get("history_hours"), 1, 24 * 31, 48),
        "group_minutes": clamp_int(raw.get("group_minutes"), 1, 1440, 5),
        "threshold_enabled": bool(raw.get("threshold_enabled", True)),
        "threshold_value": as_float(raw.get("threshold_value"), 0.0),
        "threshold_label": str(raw.get("threshold_label") or "").strip(),
    }


def legacy_page(options: dict[str, Any]) -> dict[str, Any]:
    return normalize_page(
        {
            "path": "groundwater",
            "enabled": True,
            "title": options.get("title", "Grundwasserstand und Pump Verlauf"),
            "sensor_entity": options.get(
                "sensor_entity",
                "sensor.efimmo_bw_b1_f0_r0_sen0_groundwaterlevel",
            ),
            "sensor_name": options.get("sensor_name", "Wasserstand"),
            "sensor_unit": options.get("sensor_unit", "mm"),
            "decimals": 1,
            "sensor_min": options.get("sensor_min", 500),
            "sensor_max": options.get("sensor_max", 1000),
            "binary_entity": options.get("binary_entity", "switch.pumpe_1"),
            "binary_name": options.get("binary_name", "Pumpe"),
            "history_hours": options.get("history_hours", 48),
            "group_minutes": options.get("group_minutes", 5),
            "threshold_enabled": True,
            "threshold_value": options.get("threshold_value", 660),
            "threshold_label": options.get("threshold_label", "Trigger 660 mm"),
        },
        0,
    )


def load_options() -> dict[str, Any]:
    configured = load_raw_options()

    browser_refresh_seconds = clamp_int(
        configured.get("browser_refresh_seconds"), 15, 3600, 60
    )
    ha_refresh_seconds = clamp_int(
        configured.get("ha_refresh_seconds"), 30, 3600, 300
    )
    timezone_name = str(configured.get("timezone") or "Europe/Zurich").strip()

    raw_pages = configured.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raw_pages = configured.get("graphs")

    pages: list[dict[str, Any]] = []
    if isinstance(raw_pages, list) and raw_pages:
        for index, raw_page in enumerate(raw_pages):
            if not isinstance(raw_page, dict):
                raise ValueError(f"pages[{index}] must be an object")
            page = normalize_page(raw_page, index)
            if page["enabled"]:
                pages.append(page)
    else:
        pages.append(legacy_page(configured))

    if not pages:
        raise ValueError("No enabled Public Sensors pages are configured")

    seen: set[str] = set()
    for page in pages:
        if page["path"] in seen:
            raise ValueError(f"Duplicate public path '{page['path']}'")
        seen.add(page["path"])

    return {
        "browser_refresh_seconds": browser_refresh_seconds,
        "ha_refresh_seconds": ha_refresh_seconds,
        "timezone": timezone_name,
        "pages": pages,
    }


def ha_get(path: str, params: dict[str, Any] | None = None) -> Any:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")

    response = SESSION.get(
        f"{HA_API_BASE}{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return int(dt.timestamp() * 1000)


def find_series(history: list[Any], entity_id: str) -> list[dict[str, Any]]:
    for series in history:
        if not isinstance(series, list) or not series:
            continue
        first = series[0]
        if isinstance(first, dict) and first.get("entity_id") == entity_id:
            return [item for item in series if isinstance(item, dict)]
    return []


def numeric_points(
    series: list[dict[str, Any]],
    group_minutes: int,
    start_ms: int,
    end_ms: int,
) -> list[list[float | int]]:
    bucket_ms = group_minutes * 60 * 1000
    buckets: dict[int, list[float]] = defaultdict(list)

    for item in series:
        timestamp = parse_timestamp(item.get("last_changed") or item.get("last_updated"))
        if timestamp is None:
            continue

        state = item.get("state")
        if state in (None, "unknown", "unavailable"):
            continue

        try:
            value = float(state)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue

        timestamp = max(start_ms, min(timestamp, end_ms))
        bucket = max(start_ms, (timestamp // bucket_ms) * bucket_ms)
        buckets[bucket].append(value)

    result: list[list[float | int]] = []
    for bucket in sorted(buckets):
        values = buckets[bucket]
        result.append([bucket, round(sum(values) / len(values), 6)])
    return result


def binary_points(
    series: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    fallback_value: int,
) -> list[list[int]]:
    result: list[list[int]] = []

    for item in series:
        timestamp = parse_timestamp(item.get("last_changed") or item.get("last_updated"))
        if timestamp is None:
            continue

        value = 1 if str(item.get("state", "")).lower() == "on" else 0
        if result and result[-1][1] == value:
            continue
        result.append([timestamp, value])

    if not result:
        return [[start_ms, fallback_value], [end_ms, fallback_value]]

    if result[0][0] > start_ms:
        result.insert(0, [start_ms, result[0][1]])
    else:
        result[0][0] = max(result[0][0], start_ms)

    if result[-1][0] < end_ms:
        result.append([end_ms, result[-1][1]])

    return result


def current_entity(entity_id: str) -> dict[str, Any]:
    value = ha_get(f"/states/{entity_id}")
    return value if isinstance(value, dict) else {}


def build_payload(
    page: dict[str, Any],
    browser_refresh_seconds: int,
    timezone_name: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=page["history_hours"])

    entities = [page["sensor_entity"]]
    if page["binary_entity"]:
        entities.append(page["binary_entity"])

    history = ha_get(
        f"/history/period/{utc_iso(start)}",
        params={
            "filter_entity_id": ",".join(entities),
            "end_time": utc_iso(now),
            "minimal_response": "",
            "no_attributes": "",
        },
    )
    if not isinstance(history, list):
        raise RuntimeError("Unexpected response from Home Assistant history API")

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    sensor_series = find_series(history, page["sensor_entity"])
    sensor_data = numeric_points(
        sensor_series,
        page["group_minutes"],
        start_ms,
        end_ms,
    )
    if not sensor_data:
        raise RuntimeError(
            f"No numeric history data returned for {page['sensor_entity']}"
        )

    sensor_state = current_entity(page["sensor_entity"])
    sensor_attributes = sensor_state.get("attributes", {})
    if not isinstance(sensor_attributes, dict):
        sensor_attributes = {}

    sensor_unit = page["sensor_unit"]
    if not sensor_unit:
        sensor_unit = str(sensor_attributes.get("unit_of_measurement", ""))

    sensor_name = page["sensor_name"]
    if not sensor_name:
        sensor_name = str(
            sensor_attributes.get("friendly_name", page["sensor_entity"])
        )

    try:
        current_sensor = float(sensor_state.get("state"))
        if not math.isfinite(current_sensor):
            raise ValueError("non-finite state")
    except (TypeError, ValueError):
        current_sensor = sensor_data[-1][1]

    binary_payload = None
    if page["binary_entity"]:
        binary_series = find_series(history, page["binary_entity"])
        binary_state = current_entity(page["binary_entity"])
        binary_current = str(binary_state.get("state", "unknown")).lower()
        fallback_binary = 1 if binary_current == "on" else 0
        binary_payload = {
            "name": page["binary_name"],
            "current": binary_current,
            "data": binary_points(
                binary_series,
                start_ms,
                end_ms,
                fallback_binary,
            ),
        }

    threshold_payload = None
    if page["threshold_enabled"]:
        threshold_payload = {
            "value": page["threshold_value"],
            "label": page["threshold_label"] or str(page["threshold_value"]),
        }

    return {
        "path": page["path"],
        "title": page["title"],
        "updated": utc_iso(now),
        "range": {
            "start": start_ms,
            "end": end_ms,
            "hours": page["history_hours"],
        },
        "refresh_seconds": browser_refresh_seconds,
        "timezone": timezone_name,
        "sensor": {
            "name": sensor_name,
            "unit": sensor_unit,
            "decimals": page["decimals"],
            "min": page["sensor_min"],
            "max": page["sensor_max"],
            "current": current_sensor,
            "data": sensor_data,
        },
        "binary": binary_payload,
        "threshold": threshold_payload,
    }


def refresh_cache() -> None:
    try:
        options = load_options()
    except Exception as exc:
        LOG.exception("Could not load Public Sensors configuration")
        with CACHE_LOCK:
            CACHE["global_error"] = str(exc)
        return

    pages = options["pages"]
    page_list = [{"path": page["path"], "title": page["title"]} for page in pages]

    fresh_data: dict[str, Any] = {}
    fresh_errors: dict[str, str] = {}
    fresh_times: dict[str, float] = {}

    with CACHE_LOCK:
        old_data = dict(CACHE["data"])
        old_times = dict(CACHE["updated_monotonic"])

    for page in pages:
        public_path = page["path"]
        try:
            payload = build_payload(
                page,
                options["browser_refresh_seconds"],
                options["timezone"],
            )
            fresh_data[public_path] = payload
            fresh_times[public_path] = time.monotonic()
            LOG.info(
                "Page '/%s/' refreshed: %s numeric points",
                public_path,
                len(payload["sensor"]["data"]),
            )
        except Exception as exc:
            LOG.exception("Could not refresh page '/%s/'", public_path)
            fresh_errors[public_path] = str(exc)

            if public_path in old_data:
                fresh_data[public_path] = old_data[public_path]
                fresh_times[public_path] = old_times.get(public_path, 0.0)

    with CACHE_LOCK:
        CACHE["pages"] = page_list
        CACHE["data"] = fresh_data
        CACHE["errors"] = fresh_errors
        CACHE["updated_monotonic"] = fresh_times
        CACHE["global_error"] = None


def refresh_loop() -> None:
    while True:
        refresh_cache()
        try:
            interval = load_options()["ha_refresh_seconds"]
        except Exception:
            LOG.exception("Could not read refresh interval; using 300 seconds")
            interval = 300
        time.sleep(interval)


def configured_paths() -> set[str]:
    with CACHE_LOCK:
        cached = {item["path"] for item in CACHE["pages"]}
    if cached:
        return cached

    try:
        return {page["path"] for page in load_options()["pages"]}
    except Exception:
        return set()


@APP.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    return response


@APP.before_request
def allow_read_only():
    if request.method not in ("GET", "HEAD"):
        return jsonify({"error": "read_only"}), 405
    return None


@APP.get("/")
def root():
    abort(404)


@APP.get("/<public_path>/")
def page_view(public_path: str):
    public_path = public_path.lower()
    if public_path not in configured_paths():
        abort(404)
    return render_template("index.html", public_path=public_path)


@APP.get("/<public_path>/data")
def page_data(public_path: str):
    public_path = public_path.lower()

    with CACHE_LOCK:
        known_paths = {item["path"] for item in CACHE["pages"]}
        data_by_path = dict(CACHE["data"])
        errors = dict(CACHE["errors"])
        updated_times = dict(CACHE["updated_monotonic"])
        global_error = CACHE["global_error"]

    if public_path not in known_paths:
        if public_path not in configured_paths():
            return jsonify({"error": "not_found"}), 404

    data = data_by_path.get(public_path)
    if data is None:
        return jsonify(
            {
                "error": errors.get(public_path)
                or global_error
                or "Page data is not available yet"
            }
        ), 503

    response = jsonify(
        {
            **data,
            "cache_age_seconds": round(
                max(0.0, time.monotonic() - updated_times.get(public_path, 0.0)),
                1,
            ),
            "stale": public_path in errors,
            "refresh_error": errors.get(public_path),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@APP.get("/healthz")
def healthz():
    with CACHE_LOCK:
        pages = list(CACHE["pages"])
        data_by_path = dict(CACHE["data"])
        errors = dict(CACHE["errors"])
        global_error = CACHE["global_error"]

    total = len(pages)
    ready = sum(1 for item in pages if item["path"] in data_by_path)

    if ready == 0:
        return jsonify(
            {
                "status": "starting" if total else "error",
                "ready": ready,
                "total": total,
                "error": global_error,
                "page_errors": errors,
            }
        ), 503

    status = "ok" if ready == total and not errors else "degraded"
    return jsonify(
        {
            "status": status,
            "ready": ready,
            "total": total,
            "page_errors": errors,
        }
    ), 200


def start_background_worker() -> None:
    worker = threading.Thread(
        target=refresh_loop,
        name="ha-history-refresh",
        daemon=True,
    )
    worker.start()


start_background_worker()
