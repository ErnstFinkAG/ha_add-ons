#!/usr/bin/env python3
import json
import logging
import math
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

LOG = logging.getLogger("public_sensors")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

OPTIONS_PATH = Path("/data/options.json")
HA_API_BASE = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

APP = Flask(__name__, static_folder="static", template_folder="templates")
SESSION = requests.Session()
CACHE_LOCK = threading.Lock()
CACHE: dict[str, Any] = {
    "data": None,
    "error": "Waiting for first Home Assistant history refresh",
    "updated_monotonic": 0.0,
}


def load_options() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "title": "Grundwasserstand und Pump Verlauf",
        "sensor_entity": "sensor.efimmo_bw_b1_f0_r0_sen0_groundwaterlevel",
        "sensor_name": "Wasserstand",
        "sensor_unit": "mm",
        "sensor_min": 500.0,
        "sensor_max": 1000.0,
        "binary_entity": "switch.pumpe_1",
        "binary_name": "Pumpe",
        "history_hours": 48,
        "group_minutes": 5,
        "threshold_value": 660.0,
        "threshold_label": "Trigger 660 mm",
        "browser_refresh_seconds": 60,
        "ha_refresh_seconds": 300,
        "timezone": "Europe/Zurich",
    }

    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as handle:
            configured = json.load(handle)
            if isinstance(configured, dict):
                defaults.update(configured)
    except FileNotFoundError:
        LOG.warning("%s does not exist; using defaults", OPTIONS_PATH)
    except Exception:
        LOG.exception("Could not read %s; using defaults", OPTIONS_PATH)

    defaults["history_hours"] = max(1, min(int(defaults["history_hours"]), 24 * 31))
    defaults["group_minutes"] = max(1, min(int(defaults["group_minutes"]), 1440))
    defaults["browser_refresh_seconds"] = max(
        15, min(int(defaults["browser_refresh_seconds"]), 3600)
    )
    defaults["ha_refresh_seconds"] = max(
        30, min(int(defaults["ha_refresh_seconds"]), 3600)
    )
    defaults["sensor_min"] = float(defaults["sensor_min"])
    defaults["sensor_max"] = float(defaults["sensor_max"])
    defaults["threshold_value"] = float(defaults["threshold_value"])

    if defaults["sensor_max"] <= defaults["sensor_min"]:
        raise ValueError("sensor_max must be greater than sensor_min")

    return defaults


def ha_get(path: str, params: dict[str, Any] | None = None) -> Any:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")

    url = f"{HA_API_BASE}{path}"
    response = SESSION.get(
        url,
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
        result.append([bucket, round(sum(values) / len(values), 3)])

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

    first_value = result[0][1]
    last_value = result[-1][1]

    if result[0][0] > start_ms:
        result.insert(0, [start_ms, first_value])
    else:
        result[0][0] = max(result[0][0], start_ms)

    if result[-1][0] < end_ms:
        result.append([end_ms, last_value])

    return result


def current_entity(entity_id: str) -> dict[str, Any]:
    value = ha_get(f"/states/{entity_id}")
    return value if isinstance(value, dict) else {}


def build_payload() -> dict[str, Any]:
    options = load_options()
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=options["history_hours"])

    entities = [options["sensor_entity"]]
    binary_entity = str(options.get("binary_entity", "")).strip()
    if binary_entity:
        entities.append(binary_entity)

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

    sensor_series = find_series(history, options["sensor_entity"])
    sensor_data = numeric_points(
        sensor_series,
        options["group_minutes"],
        start_ms,
        end_ms,
    )

    sensor_state = current_entity(options["sensor_entity"])
    sensor_attributes = sensor_state.get("attributes", {})
    if not isinstance(sensor_attributes, dict):
        sensor_attributes = {}

    sensor_unit = str(options.get("sensor_unit", "")).strip()
    if not sensor_unit:
        sensor_unit = str(sensor_attributes.get("unit_of_measurement", ""))

    sensor_name = str(options.get("sensor_name", "")).strip()
    if not sensor_name:
        sensor_name = str(
            sensor_attributes.get("friendly_name", options["sensor_entity"])
        )

    try:
        current_sensor = float(sensor_state.get("state"))
        if not math.isfinite(current_sensor):
            raise ValueError("non-finite state")
    except (TypeError, ValueError):
        current_sensor = sensor_data[-1][1] if sensor_data else None

    binary_payload = None
    if binary_entity:
        binary_series = find_series(history, binary_entity)
        binary_state = current_entity(binary_entity)
        binary_current = str(binary_state.get("state", "unknown")).lower()
        fallback_binary = 1 if binary_current == "on" else 0
        binary_data = binary_points(
            binary_series,
            start_ms,
            end_ms,
            fallback_binary,
        )

        binary_payload = {
            "entity_id": binary_entity,
            "name": str(options.get("binary_name", "Binary sensor")),
            "current": binary_current,
            "data": binary_data,
        }

    if not sensor_data:
        raise RuntimeError(
            f"No numeric history data returned for {options['sensor_entity']}"
        )

    payload = {
        "title": str(options["title"]),
        "updated": utc_iso(now),
        "range": {
            "start": start_ms,
            "end": end_ms,
            "hours": options["history_hours"],
        },
        "refresh_seconds": options["browser_refresh_seconds"],
        "timezone": str(options["timezone"]),
        "sensor": {
            "entity_id": options["sensor_entity"],
            "name": sensor_name,
            "unit": sensor_unit,
            "min": options["sensor_min"],
            "max": options["sensor_max"],
            "current": current_sensor,
            "data": sensor_data,
        },
        "binary": binary_payload,
        "threshold": {
            "value": options["threshold_value"],
            "label": str(options["threshold_label"]),
        },
    }

    return payload


def refresh_cache() -> None:
    try:
        payload = build_payload()
    except Exception as exc:
        LOG.exception("Could not refresh Home Assistant history")
        with CACHE_LOCK:
            CACHE["error"] = str(exc)
        return

    with CACHE_LOCK:
        CACHE["data"] = payload
        CACHE["error"] = None
        CACHE["updated_monotonic"] = time.monotonic()

    LOG.info(
        "History refreshed: %s numeric points",
        len(payload["sensor"]["data"]),
    )


def refresh_loop() -> None:
    while True:
        refresh_cache()

        try:
            interval = load_options()["ha_refresh_seconds"]
        except Exception:
            LOG.exception("Could not read refresh interval; using 300 seconds")
            interval = 300

        time.sleep(interval)


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
def index():
    return render_template("index.html")


@APP.get("/api/data")
def api_data():
    with CACHE_LOCK:
        data = CACHE["data"]
        error = CACHE["error"]
        updated_monotonic = CACHE["updated_monotonic"]

    if data is None:
        return jsonify({"error": error or "No data available"}), 503

    response = jsonify(
        {
            **data,
            "cache_age_seconds": round(
                max(0.0, time.monotonic() - updated_monotonic), 1
            ),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@APP.get("/healthz")
def healthz():
    with CACHE_LOCK:
        ready = CACHE["data"] is not None
        error = CACHE["error"]

    status = 200 if ready else 503
    return jsonify({"status": "ok" if ready else "starting", "error": error}), status


def start_background_worker() -> None:
    worker = threading.Thread(
        target=refresh_loop,
        name="ha-history-refresh",
        daemon=True,
    )
    worker.start()


start_background_worker()
