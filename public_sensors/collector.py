#!/usr/bin/env python3
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

LOG = logging.getLogger("public_sensors.collector")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

OPTIONS_PATH = Path("/data/options.json")
CACHE_DIR = Path("/run/public-sensors")
CACHE_PATH = CACHE_DIR / "cache.json"
CACHE_TMP_PATH = CACHE_DIR / "cache.json.tmp"

HA_API_BASE = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PUBLIC_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

SESSION = requests.Session()


def clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


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


def parse_duration_minutes(value: Any, default_minutes: int) -> int:
    if value is None:
        return default_minutes
    if isinstance(value, (int, float)):
        return max(1, int(value))

    text = str(value).strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|sec|m|min|h|d)", text)
    if not match:
        return default_minutes

    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "ms": 1 / 60000,
        "s": 1 / 60,
        "sec": 1 / 60,
        "m": 1,
        "min": 1,
        "h": 60,
        "d": 1440,
    }[unit]
    return max(1, int(round(amount * multiplier)))


def parse_graph_hours(value: Any, default_hours: int = 24) -> int:
    minutes = parse_duration_minutes(value, default_hours * 60)
    return max(1, min(int(math.ceil(minutes / 60)), 24 * 31))


def load_options() -> dict[str, Any]:
    with OPTIONS_PATH.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict):
        raise ValueError("options.json must contain an object")

    pages_raw = raw.get("pages", [])
    if not isinstance(pages_raw, list):
        raise ValueError("pages must be a list")

    pages: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for index, item in enumerate(pages_raw):
        if not isinstance(item, dict):
            raise ValueError(f"pages[{index}] must be an object")

        path = str(item.get("path") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        config_yaml = str(item.get("yaml") or "").strip()
        entity_id = str(item.get("entity_id") or "").strip().lower()

        if not PUBLIC_PATH_RE.fullmatch(path):
            raise ValueError(
                f"Invalid path '{path}' in pages[{index}]. "
                "Use lowercase letters, numbers, '-' or '_'."
            )
        if path in seen_paths:
            raise ValueError(f"Duplicate public path '{path}'")
        seen_paths.add(path)

        if not name:
            raise ValueError(f"pages[{index}] has no name")

        if bool(config_yaml) == bool(entity_id):
            raise ValueError(
                f"Page '{path}' must define exactly one of yaml or entity_id"
            )

        if entity_id and not ENTITY_ID_RE.fullmatch(entity_id):
            raise ValueError(f"Page '{path}' has invalid entity_id '{entity_id}'")

        pages.append(
            {
                "path": path,
                "name": name,
                "yaml": config_yaml,
                "entity_id": entity_id,
            }
        )

    return {
        "browser_refresh_seconds": clamp_int(
            raw.get("browser_refresh_seconds"), 15, 3600, 60
        ),
        "ha_refresh_seconds": clamp_int(
            raw.get("ha_refresh_seconds"), 30, 3600, 300
        ),
        "timezone": str(raw.get("timezone") or "UTC").strip(),
        "show_index": bool(raw.get("show_index", True)),
        "pages": pages,
    }


def ha_get(path: str, params: dict[str, Any] | None = None) -> Any:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not available to collector")

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


def current_entity(entity_id: str) -> dict[str, Any]:
    value = ha_get(f"/states/{entity_id}")
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected state response for {entity_id}")
    return value


def public_state_copy(state: dict[str, Any]) -> dict[str, Any]:
    attrs = state.get("attributes")
    if not isinstance(attrs, dict):
        attrs = {}

    return {
        "entity_id": str(state.get("entity_id") or ""),
        "state": state.get("state"),
        "attributes": attrs,
        "last_changed": state.get("last_changed"),
        "last_updated": state.get("last_updated"),
    }


def find_series(history: list[Any], entity_id: str) -> list[dict[str, Any]]:
    for series in history:
        if not isinstance(series, list) or not series:
            continue
        first = series[0]
        if isinstance(first, dict) and first.get("entity_id") == entity_id:
            return [item for item in series if isinstance(item, dict)]
    return []


def aggregate_numeric(
    series: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    group_minutes: int,
    function: str,
) -> list[list[float | int]]:
    bucket_ms = max(1, group_minutes) * 60 * 1000
    buckets: dict[int, list[float]] = defaultdict(list)

    for item in series:
        timestamp = parse_timestamp(item.get("last_changed") or item.get("last_updated"))
        if timestamp is None or timestamp < start_ms or timestamp > end_ms:
            continue

        state = item.get("state")
        if state in (None, "unknown", "unavailable"):
            continue

        try:
            number = float(state)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue

        bucket = max(start_ms, (timestamp // bucket_ms) * bucket_ms)
        buckets[bucket].append(number)

    result: list[list[float | int]] = []
    for bucket in sorted(buckets):
        values = buckets[bucket]
        if function == "min":
            value = min(values)
        elif function == "max":
            value = max(values)
        elif function == "last":
            value = values[-1]
        elif function == "sum":
            value = sum(values)
        else:
            value = sum(values) / len(values)

        result.append([bucket, round(value, 6)])

    return result


def binary_history(
    series: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    fallback_state: str,
) -> list[list[int]]:
    def binary_value(state: Any) -> int:
        text = str(state or "").lower()
        return 1 if text in {"on", "open", "home", "active", "true"} else 0

    result: list[list[int]] = []
    for item in series:
        timestamp = parse_timestamp(item.get("last_changed") or item.get("last_updated"))
        if timestamp is None:
            continue
        value = binary_value(item.get("state"))
        if result and result[-1][1] == value:
            continue
        result.append([timestamp, value])

    fallback = binary_value(fallback_state)
    if not result:
        return [[start_ms, fallback], [end_ms, fallback]]

    if result[0][0] > start_ms:
        result.insert(0, [start_ms, result[0][1]])
    else:
        result[0][0] = max(start_ms, result[0][0])

    if result[-1][0] < end_ms:
        result.append([end_ms, result[-1][1]])

    return result


def collect_entity_ids(value: Any) -> set[str]:
    result: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_text = str(key).lower()
                if key_text in {"entity", "entity_id"} and isinstance(child, str):
                    candidate = child.strip().lower()
                    if ENTITY_ID_RE.fullmatch(candidate):
                        result.add(candidate)
                elif key_text == "entities" and isinstance(child, list):
                    for entry in child:
                        if isinstance(entry, str):
                            candidate = entry.strip().lower()
                            if ENTITY_ID_RE.fullmatch(candidate):
                                result.add(candidate)
                        else:
                            visit(entry)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            candidate = node.strip().lower()
            if ENTITY_ID_RE.fullmatch(candidate):
                result.add(candidate)

    visit(value)
    return result


def parse_axis(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "id": f"axis-{index}",
            "min": None,
            "max": None,
            "opposite": False,
            "title": "",
        }

    def finite_or_none(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    title = ""
    apex = raw.get("apex_config")
    if isinstance(apex, dict):
        title_cfg = apex.get("title")
        if isinstance(title_cfg, dict):
            title = str(title_cfg.get("text") or "")

    return {
        "id": str(raw.get("id") or f"axis-{index}"),
        "min": finite_or_none(raw.get("min")),
        "max": finite_or_none(raw.get("max")),
        "opposite": bool(raw.get("opposite", False)),
        "title": title,
    }


def extract_annotations(config: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    apex = config.get("apex_config")
    if not isinstance(apex, dict):
        return result
    annotations = apex.get("annotations")
    if not isinstance(annotations, dict):
        return result
    yaxis = annotations.get("yaxis")
    if not isinstance(yaxis, list):
        return result

    for item in yaxis:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("y"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue

        label = item.get("label")
        text = str(label.get("text") or "") if isinstance(label, dict) else ""
        result.append(
            {
                "value": value,
                "label": text or str(value),
                "axis_index": clamp_int(item.get("yAxisIndex"), 0, 20, 0),
            }
        )
    return result


def build_entity_payload(
    page: dict[str, str],
    browser_refresh_seconds: int,
    timezone_name: str,
) -> dict[str, Any]:
    state = current_entity(page["entity_id"])
    return {
        "mode": "entity",
        "path": page["path"],
        "name": page["name"],
        "updated": utc_iso(datetime.now(timezone.utc)),
        "refresh_seconds": browser_refresh_seconds,
        "timezone": timezone_name,
        "entity": public_state_copy(state),
    }


def build_yaml_payload(
    page: dict[str, str],
    browser_refresh_seconds: int,
    timezone_name: str,
) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(page["yaml"])
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML for page '{page['path']}': {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"YAML for page '{page['path']}' must contain an object")

    referenced_entities = sorted(collect_entity_ids(parsed))
    if not referenced_entities:
        raise ValueError(
            f"YAML for page '{page['path']}' does not contain any entity references"
        )

    current_states = {
        entity_id: current_entity(entity_id)
        for entity_id in referenced_entities
    }

    series_raw = parsed.get("series")
    chart_series: list[dict[str, Any]] = []

    graph_hours = parse_graph_hours(parsed.get("graph_span"), 24)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=graph_hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    axes_raw = parsed.get("yaxis")
    axes = (
        [parse_axis(item, index) for index, item in enumerate(axes_raw)]
        if isinstance(axes_raw, list)
        else []
    )
    if not axes:
        axes = [
            {
                "id": "default",
                "min": None,
                "max": None,
                "opposite": False,
                "title": "",
            }
        ]

    if isinstance(series_raw, list):
        history_entities: list[str] = []
        specs: list[dict[str, Any]] = []

        for raw_series in series_raw:
            if not isinstance(raw_series, dict):
                continue

            entity_id = str(raw_series.get("entity") or "").strip().lower()
            if not ENTITY_ID_RE.fullmatch(entity_id):
                continue

            state = current_states.get(entity_id, {})
            attrs = state.get("attributes") if isinstance(state, dict) else {}
            attrs = attrs if isinstance(attrs, dict) else {}

            group_cfg = raw_series.get("group_by")
            group_minutes = 1
            function = "avg"
            if isinstance(group_cfg, dict):
                group_minutes = parse_duration_minutes(
                    group_cfg.get("duration"), 1
                )
                function = str(group_cfg.get("func") or "avg").lower()

            specs.append(
                {
                    "entity_id": entity_id,
                    "name": str(
                        raw_series.get("name")
                        or attrs.get("friendly_name")
                        or entity_id
                    ),
                    "axis_id": str(
                        raw_series.get("yaxis_id") or axes[0]["id"]
                    ),
                    "type": str(raw_series.get("type") or "line"),
                    "curve": str(raw_series.get("curve") or ""),
                    "stroke_width": raw_series.get("stroke_width", 2),
                    "group_minutes": group_minutes,
                    "function": function,
                }
            )
            history_entities.append(entity_id)

        if specs:
            history = ha_get(
                f"/history/period/{utc_iso(start)}",
                params={
                    "filter_entity_id": ",".join(
                        sorted(set(history_entities))
                    ),
                    "end_time": utc_iso(now),
                    "minimal_response": "",
                    "no_attributes": "",
                },
            )
            if not isinstance(history, list):
                raise RuntimeError(
                    "Unexpected response from Home Assistant history API"
                )

            for spec in specs:
                entity_id = spec["entity_id"]
                series = find_series(history, entity_id)
                current_state = str(
                    current_states[entity_id].get("state") or ""
                )
                numeric_data = aggregate_numeric(
                    series,
                    start_ms,
                    end_ms,
                    spec["group_minutes"],
                    spec["function"],
                )

                if numeric_data:
                    binary = False
                    data = numeric_data
                else:
                    binary = True
                    data = binary_history(
                        series,
                        start_ms,
                        end_ms,
                        current_state,
                    )

                attrs = current_states[entity_id].get("attributes")
                attrs = attrs if isinstance(attrs, dict) else {}

                chart_series.append(
                    {
                        "name": spec["name"],
                        "axis_id": spec["axis_id"],
                        "type": spec["type"],
                        "curve": spec["curve"],
                        "stroke_width": spec["stroke_width"],
                        "binary": binary,
                        "unit": str(
                            attrs.get("unit_of_measurement") or ""
                        ),
                        "current": current_state,
                        "data": data,
                    }
                )

    header = parsed.get("header")
    yaml_title = (
        str(header.get("title") or "")
        if isinstance(header, dict)
        else ""
    )

    public_entities = [
        public_state_copy(current_states[entity_id])
        for entity_id in referenced_entities
    ]

    # The full YAML is deliberately not copied into the public cache.
    # This prevents accidental secrets or executable card snippets in the
    # private configuration from being exposed to the public browser.
    return {
        "mode": "yaml",
        "path": page["path"],
        "name": page["name"],
        "title": yaml_title or page["name"],
        "updated": utc_iso(now),
        "refresh_seconds": browser_refresh_seconds,
        "timezone": timezone_name,
        "card_type": str(parsed.get("type") or ""),
        "range": {
            "start": start_ms,
            "end": end_ms,
            "hours": graph_hours,
        },
        "chart": {
            "axes": axes,
            "annotations": extract_annotations(parsed),
            "series": chart_series,
            "show_now": bool(
                isinstance(parsed.get("now"), dict)
                and parsed.get("now", {}).get("show", False)
            ),
        },
        "entities": public_entities,
    }


def build_page_payload(
    page: dict[str, str],
    browser_refresh_seconds: int,
    timezone_name: str,
) -> dict[str, Any]:
    if page["entity_id"]:
        return build_entity_payload(
            page,
            browser_refresh_seconds,
            timezone_name,
        )
    return build_yaml_payload(
        page,
        browser_refresh_seconds,
        timezone_name,
    )


def write_cache(payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with CACHE_TMP_PATH.open("w", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())

    os.chmod(CACHE_TMP_PATH, 0o644)
    os.replace(CACHE_TMP_PATH, CACHE_PATH)


def refresh() -> int:
    options = load_options()
    pages = options["pages"]

    data: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for page in pages:
        path = page["path"]
        try:
            data[path] = build_page_payload(
                page,
                options["browser_refresh_seconds"],
                options["timezone"],
            )
            LOG.info(
                "Page '/%s/' refreshed in %s mode",
                path,
                data[path]["mode"],
            )
        except Exception as exc:
            LOG.exception("Could not refresh page '/%s/'", path)
            errors[path] = str(exc)

    payload = {
        "generated": utc_iso(datetime.now(timezone.utc)),
        "show_index": options["show_index"],
        "pages": [
            {"path": page["path"], "name": page["name"]}
            for page in pages
        ],
        "data": data,
        "errors": errors,
    }
    write_cache(payload)
    return options["ha_refresh_seconds"]


def main() -> None:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")

    while True:
        interval = 300
        try:
            interval = refresh()
        except Exception:
            LOG.exception("Could not refresh Public Sensors cache")
            try:
                interval = load_options()["ha_refresh_seconds"]
            except Exception:
                interval = 300
        time.sleep(interval)


if __name__ == "__main__":
    main()
