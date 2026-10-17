"""Bridge between the Panasonic HPM web interface and Home Assistant via MQTT discovery."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import queue
import re
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from .hpm import Datapoint, Editor, HpmClient, HpmError, Page, menu_usefulness, parse_info, parse_menu_page, parse_page

_LOGGER = logging.getLogger(__name__)

UNIT_CLASSES = {
    "°C": ("temperature", "measurement"),
    "K": (None, "measurement"),
    "%": (None, "measurement"),
    "Hz": ("frequency", "measurement"),
    "W": ("power", "measurement"),
    "kW": ("power", "measurement"),
    "kWh": ("energy", "total_increasing"),
    "bar": ("pressure", "measurement"),
    "h": ("duration", "total_increasing"),
    "l/min": ("volume_flow_rate", "measurement"),
}
UNIT_ALIASES = {"°": "°C", "Â°C": "°C", "C": "°C"}


def slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", text.lower()).strip("_")


class Bridge:
    def __init__(self, options: dict, mqtt_conf: dict):
        self.opt = options
        self.scan_status = "Waiting for the first scan"
        self.client = HpmClient(options["host"], options.get("username", ""), options.get("password", ""),
                                request_delay=float(options.get("request_delay", 0.3)),
                                session_file=options.get("session_file", "/data/hpm_session"),
                                access_code=options.get("access_code", ""))
        self.node = slug(options.get("device_name") or "Aquarea HPM") or "hpm"
        self.base = f"{options.get('topic_prefix', 'hpm')}/{self.node}"
        self.disc_prefix = options.get("discovery_prefix", "homeassistant")
        self.avail_topic = f"{self.base}/status"
        self.dump_dir = Path(options.get("dump_dir", "/share/aquarea_hpm")) if options.get("dump_html") else None
        self.state_file = Path(options.get("state_file", "/data/entities.json"))
        self.cache_file = Path(options.get("cache_file", "/data/scan_cache.json"))

        self.pages: dict[str, Page] = {}
        self.points: dict[str, Datapoint] = {}
        self.editors: dict[str, Editor] = {}
        self.object_ids: dict[str, str] = {}  # object id -> datapoint id
        self.last_published: dict[str, str] = {}
        self.commands: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.rescan_requested = False

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hpm2mqtt-{self.node}")
        if mqtt_conf.get("username"):
            self.mqtt.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password") or None)
        if mqtt_conf.get("ssl"):
            self.mqtt.tls_set()
        self.mqtt.will_set(self.avail_topic, "offline", retain=True)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message
        self.mqtt_conf = mqtt_conf

    # -- MQTT --------------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            _LOGGER.error("MQTT connection refused: %s", reason_code)
            return
        _LOGGER.info("Connected to MQTT broker")
        client.subscribe(f"{self.base}/+/set")
        client.subscribe(f"{self.disc_prefix}/status")
        if self.points:
            self.publish_discovery()
            self.publish_states(force=True)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace").strip()
        if msg.topic == f"{self.disc_prefix}/status":
            if payload == "online" and self.points:
                self.commands.put(("__republish__", ""))
            return
        object_id = msg.topic[len(self.base) + 1:].rsplit("/", 1)[0]
        self.commands.put((object_id, payload))

    def _pub(self, topic: str, payload, retain: bool = True):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.mqtt.publish(topic, payload, qos=1, retain=retain)

    def connect_mqtt(self):
        host, port = self.mqtt_conf["host"], int(self.mqtt_conf.get("port") or 1883)
        user = self.mqtt_conf.get("username") or "(none)"
        delay = 5
        while True:
            _LOGGER.info("Connecting to MQTT broker %s:%s as %s", host, port, user)
            try:
                self.mqtt.connect(host, port, keepalive=60)
                break
            except OSError as err:
                # Broker not up yet (e.g. Mosquitto still starting after a reboot) or wrong address.
                _LOGGER.warning("MQTT broker %s:%s not reachable (%s); retrying in %ss. "
                                "Check that the Mosquitto broker add-on is running, or the mqtt_host/mqtt_port options.",
                                host, port, err, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
        self.mqtt.loop_start()

    # -- discovery ---------------------------------------------------------------------
    @staticmethod
    def object_id(dp_id: str) -> str:
        return "dp_" + slug(dp_id)

    def device_info(self) -> dict:
        return {
            "identifiers": [f"panasonic_hpm_{self.node}"],
            "name": self.opt.get("device_name") or "Aquarea HPM",
            "manufacturer": "Panasonic",
            "model": "Aquarea Heat Pump Manager (PAW-HPM)",
            "configuration_url": self.client.base,
        }

    @staticmethod
    def _is_menu(dp: Datapoint) -> bool:
        return dp.page.startswith("menu:")

    def entity_config(self, dp: Datapoint) -> tuple[str, dict]:
        oid = self.object_id(dp.id)
        unit = UNIT_ALIASES.get(dp.unit, dp.unit) if dp.unit else None
        base_name = dp.long_name or (dp.label if not dp.label.startswith("Wert ") else dp.id)
        name = f"{dp.page_title} {base_name}".strip() if dp.page_title and dp.page_title not in base_name else base_name
        cfg = {
            "name": name,
            "unique_id": f"{self.node}_{oid}",
            "object_id": f"{self.node}_{oid}",
            "state_topic": f"{self.base}/{oid}/state",
            "availability_topic": self.avail_topic,
            "device": self.device_info(),
            "json_attributes_topic": f"{self.base}/{oid}/attributes",
        }
        editor = self.editors.get(dp.id) if dp.writable and self.opt.get("enable_control", True) else None
        if editor is not None and editor.kind == "select":
            component = "select"
            cfg["command_topic"] = f"{self.base}/{oid}/set"
            cfg["options"] = [text for _, text in editor.options]
            cfg["icon"] = "mdi:form-select"
        elif editor is not None and editor.kind == "number":
            component = "number"
            cfg["command_topic"] = f"{self.base}/{oid}/set"
            cfg["mode"] = "box"
            cfg["min"] = editor.minimum if editor.minimum is not None else -100000
            cfg["max"] = editor.maximum if editor.maximum is not None else 100000
            cfg["step"] = editor.step or (10 ** -dp.decimals if dp.decimals else 1)
            if unit:
                cfg["unit_of_measurement"] = unit
            if UNIT_CLASSES.get(unit, (None,))[0] == "temperature":
                cfg["device_class"] = "temperature"
        elif editor is not None:
            component = "text"
            cfg["command_topic"] = f"{self.base}/{oid}/set"
        else:
            component = "sensor"
            if isinstance(dp.value, (int, float)):
                if unit:
                    cfg["unit_of_measurement"] = unit
                device_class, state_class = UNIT_CLASSES.get(unit, (None, "measurement"))
                if device_class:
                    cfg["device_class"] = device_class
                cfg["state_class"] = state_class
                cfg["suggested_display_precision"] = dp.decimals
        if self._is_menu(dp):
            cfg["entity_category"] = "config" if component != "sensor" else "diagnostic"
        return component, cfg

    def _load_previous(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def publish_discovery(self, cleanup: bool = True):
        published = {}
        for dp in self.points.values():
            component, cfg = self.entity_config(dp)
            topic = f"{self.disc_prefix}/{component}/{self.node}/{self.object_id(dp.id)}/config"
            self._pub(topic, cfg)
            published[topic] = True
        # Rescan button and connectivity sensor
        for component, oid, cfg in (
            ("button", "rescan", {"name": "Rescan values", "command_topic": f"{self.base}/rescan/set", "entity_category": "diagnostic", "icon": "mdi:magnify-scan"}),
            ("binary_sensor", "connected", {"name": "Connection", "state_topic": f"{self.base}/connected/state", "device_class": "connectivity", "entity_category": "diagnostic"}),
        ):
            cfg.update({"unique_id": f"{self.node}_{oid}", "object_id": f"{self.node}_{oid}", "device": self.device_info()})
            if component == "button":
                cfg["availability_topic"] = self.avail_topic
            topic = f"{self.disc_prefix}/{component}/{self.node}/{oid}/config"
            self._pub(topic, cfg)
            published[topic] = True
        if cleanup:  # not for the partial list published while a scan is still running
            for topic in self._load_previous():
                if topic not in published:
                    self._pub(topic, "")  # remove entities that disappeared or changed type
            try:
                self.state_file.write_text(json.dumps(published))
            except OSError as err:
                _LOGGER.debug("Could not store entity list: %s", err)
        self._pub(self.avail_topic, "online")
        _LOGGER.info("Published %d entities (%d controllable)", len(self.points),
                     sum(1 for d in self.points.values() if d.id in self.editors))

    def _dump(self, name: str, html: str):
        if self.dump_dir is None:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        (self.dump_dir / (re.sub(r"[^0-9A-Za-z._=-]+", "_", name) + ".html")).write_text(html, encoding="utf-8")

    def discover(self):
        _LOGGER.info("Scanning the HPM web interface for values ...")
        if self.dump_dir is not None and self.dump_dir.is_dir():
            for old in self.dump_dir.glob("*.html"):  # pages from earlier scans/versions would be misleading
                old.unlink(missing_ok=True)
        seen = [0]

        def on_page(key, html):
            seen[0] += 1
            self.scan_status = f"Scan running: {seen[0]} pages read so far (wait for it to finish before downloading the zip)"
            self._dump(f"page_{key}", html)

        self.scan_status = "Scan running"
        self.client.on_page = on_page
        extra = list(self.opt.get("extra_pages", []) or [])
        max_pages = int(self.opt.get("max_pages", 150))
        pages = self.client.crawl(max_pages, tuple(extra))
        menu_values = str(self.opt.get("menu_values", "useful")).lower()
        if self.opt.get("include_menus", True) and menu_values != "none":
            # WEB-FB: the HPM's full parameter menu tree (navigated step by step like a person would)
            try:
                pages.update(self.client.walk_menus(int(self.opt.get("max_menu_pages", 500))))
            except Exception as err:  # noqa: BLE001 - the overview pages are still worth publishing
                _LOGGER.warning("Could not read the WEB-FB menus: %s", err)
        settings = self.opt.get("menu_settings", True)
        for key, page in pages.items():
            if not key.startswith("menu:"):
                continue
            kept = []
            for dp in page.datapoints:
                use = menu_usefulness(dp.menu_title, dp.label)
                if use is None and menu_values == "useful":
                    continue
                # only the recommended settings can be changed; everything else stays read-only
                dp.writable = dp.writable and use == "set" and bool(settings)
                kept.append(dp)
            page.datapoints = kept
        points: dict[str, Datapoint] = {}
        for page in pages.values():
            for dp in page.datapoints:
                if dp.id not in points or (dp.writable and not points[dp.id].writable):
                    points[dp.id] = dp
        no_value = []
        pages_read = seen[0]
        for n, dp in enumerate(list(points.values()), 1):
            if dp.inline_editor is not None or dp.long_name or self._is_menu(dp):
                continue
            self.scan_status = f"Scan running: {pages_read} pages read, reading value names ({n}/{len(points)})"
            try:
                html = self.client.info(dp.id)
            except Exception as err:  # noqa: BLE001 - a missing name is not worth stopping the scan
                _LOGGER.debug("No info page for %s: %s", dp.id, err)
                continue
            self._dump(f"info_{dp.id}", html)
            dp.long_name, has_value = parse_info(html)
            if not has_value and not dp.writable:
                _LOGGER.debug("Skipping %s (%s): only a link to a status page", dp.id, dp.raw)
                no_value.append(dp.id)
        for dp_id in no_value:
            points.pop(dp_id, None)
        editors: dict[str, Editor] = {}
        writable = [dp for dp in points.values() if dp.writable] if self.opt.get("enable_control", True) else []
        if writable:
            # Publish the read-only values right away; opening every editor takes a few minutes
            # (each menu setting means navigating to its menu again).
            self.pages = {k: p for k, p in pages.items() if p.datapoints}
            self.points = {i: dp for i, dp in points.items() if not dp.writable}
            self.editors = {}
            self.object_ids = {self.object_id(i): i for i in self.points}
            self.last_published.clear()
            self.publish_discovery(cleanup=False)
            self.publish_states(force=True)
            _LOGGER.info("Published %d read-only values; checking %d changeable settings next", len(self.points), len(writable))
            for n, dp in enumerate(writable, 1):
                self.scan_status = (f"Scan running: {pages_read} pages read, {len(self.points)} values published, "
                                    f"checking settings ({n}/{len(writable)})")
                try:
                    editor, html = self.client.probe_editor(dp)
                except Exception as err:  # noqa: BLE001 - one odd value must not stop the scan
                    _LOGGER.warning("Could not open editor for %s (%s): %s", dp.id, dp.label, err)
                    continue
                self._dump(f"editor_{dp.id}", html)
                if editor is None:
                    _LOGGER.warning("Value %s (%s) looks changeable but its editor page was not understood; "
                                    "enable dump_html and share the file to add support", dp.id, dp.label)
                    continue
                editors[dp.id] = editor
                if editor.name:
                    dp.long_name = editor.name
        self.pages = {k: p for k, p in pages.items() if p.datapoints}
        self.points, self.editors = points, editors
        self.object_ids = {self.object_id(i): i for i in points}
        self.last_published.clear()
        _LOGGER.info("Found %d values on %d pages, %d of them controllable", len(points), len(self.pages), len(editors))
        self.publish_discovery()
        self.publish_states(force=True)
        self.save_scan_cache()
        self.scan_status = f"Scan finished {time.strftime('%H:%M')}: {seen[0]} pages read, {len(points)} values"

    # -- scan cache --------------------------------------------------------------------
    # The result of a full scan is kept in /data so a restart polls right away instead of
    # walking the whole web interface again. Changed options or a new add-on version
    # (different code) invalidate it.
    _CACHE_OPTIONS = ("host", "username", "access_code", "include_menus", "menu_values", "menu_settings",
                      "enable_control", "extra_pages", "max_pages", "max_menu_pages")

    def _cache_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for module in sorted(Path(__file__).parent.glob("*.py")):
            digest.update(module.read_bytes())
        digest.update(json.dumps({k: self.opt.get(k) for k in self._CACHE_OPTIONS}, sort_keys=True).encode())
        return digest.hexdigest()

    def save_scan_cache(self):
        data = {"fingerprint": self._cache_fingerprint(), "time": time.time(),
                "pages": {k: p.title for k, p in self.pages.items()},
                "points": [dataclasses.asdict(dp) for dp in self.points.values()],
                "editors": {i: dataclasses.asdict(e) for i, e in self.editors.items()}}
        try:
            self.cache_file.write_text(json.dumps(data, ensure_ascii=False))
        except OSError as err:
            _LOGGER.debug("Could not store the scan result: %s", err)

    def load_scan_cache(self, max_age: float) -> Optional[float]:
        """Restore the last scan if it is still valid; returns its age in seconds."""
        try:
            data = json.loads(self.cache_file.read_text())
            age = time.time() - float(data["time"])
            if data.get("fingerprint") != self._cache_fingerprint() or not 0 <= age < max_age:
                return None

            def editor(d):
                return Editor(**{**d, "options": [tuple(o) for o in d.get("options", [])]}) if d else None

            points = {}
            for d in data["points"]:
                dp = Datapoint(**{**d, "inline_editor": editor(d.get("inline_editor")),
                                  "change": tuple(d["change"]) if d.get("change") else None})
                points[dp.id] = dp
            editors = {i: editor(e) for i, e in data["editors"].items()}
            pages = {k: Page(k, title) for k, title in data["pages"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not points:
            return None
        self.pages, self.points, self.editors = pages, points, editors
        self.object_ids = {self.object_id(i): i for i in points}
        self.last_published.clear()
        _LOGGER.info("Using the last scan from %s (%d values); the next full scan runs in %.1f h or on Rescan values",
                     time.strftime("%d.%m. %H:%M", time.localtime(data["time"])), len(points), (max_age - age) / 3600)
        self.scan_status = (f"Using the scan from {time.strftime('%d.%m. %H:%M', time.localtime(data['time']))}: "
                            f"{len(points)} values (press Rescan values to scan again)")
        self.publish_discovery()
        return age

    # -- states ------------------------------------------------------------------------
    def publish_states(self, force: bool = False):
        for dp in self.points.values():
            oid = self.object_id(dp.id)
            state = str(dp.value)
            editor = self.editors.get(dp.id)
            if editor is not None and editor.kind == "select":
                state = next((t for _, t in editor.options if t.lower() == state.lower()), state)
            if force or self.last_published.get(oid) != state:
                self._pub(f"{self.base}/{oid}/state", state)
                self._pub(f"{self.base}/{oid}/attributes",
                          {"hpm_id": dp.id, "page": dp.page, "page_title": dp.page_title, "raw": dp.raw, "label": dp.label,
                           "name": dp.long_name})
                self.last_published[oid] = state

    def poll_page(self, key: str):
        if key.startswith("menu:"):
            path = tuple(int(b) for b in key[5:].split("/") if b)
            names = tuple(self.pages[key].title.split(" / ")) if key in self.pages else ()
            html = self.client.get_menu(path, names)
            page = parse_menu_page(html, key, self.pages[key].title if key in self.pages else key)
        else:
            html = self.client.get(key)
            page = parse_page(html, key, self.pages[key].title if key in self.pages else key)
        for fresh in page.datapoints:
            dp = self.points.get(fresh.id)
            if dp is None:
                continue
            dp.value, dp.raw = fresh.value, fresh.raw
            if fresh.form:
                dp.form = fresh.form
            if fresh.inline_editor is not None:
                dp.inline_editor = fresh.inline_editor
                self.editors[dp.id] = fresh.inline_editor

    def handle_command(self, object_id: str, payload: str):
        if object_id == "__republish__":
            self.publish_discovery()
            self.publish_states(force=True)
            return
        if object_id == "rescan":
            self.rescan_requested = True
            return
        dp_id = self.object_ids.get(object_id)
        if dp_id is None or dp_id not in self.editors:
            _LOGGER.warning("Ignoring command for unknown or read-only entity %s", object_id)
            return
        dp = self.points[dp_id]
        try:
            self.client.write(dp, payload)
            time.sleep(1)
            self.poll_page(dp.page)
        except (HpmError, ValueError, OSError) as err:
            _LOGGER.error("Setting %s (%s) to %s failed: %s", dp.id, dp.label, payload, err)
        self.last_published.pop(object_id, None)  # make HA show the real value again
        self.publish_states()

    def _drain_commands(self, until: float):
        while True:
            timeout = until - time.monotonic()
            if timeout <= 0:
                return
            try:
                object_id, payload = self.commands.get(timeout=timeout)
            except queue.Empty:
                return
            self.handle_command(object_id, payload)
            if self.rescan_requested:
                return

    def _set_connected(self, ok: bool):
        self._pub(f"{self.base}/connected/state", "ON" if ok else "OFF")

    def run(self):
        self.connect_mqtt()
        scan_interval = max(10, int(self.opt.get("scan_interval", 60)))
        param_interval = max(scan_interval, int(self.opt.get("parameter_interval", 900)))
        rescan_interval = max(1, float(self.opt.get("rescan_hours", 24))) * 3600
        last_param_poll = last_scan = 0.0
        cached_age = self.load_scan_cache(rescan_interval)
        if cached_age is not None:
            last_scan = time.monotonic() - cached_age
        backoff = 30
        while True:
            try:
                now = time.monotonic()
                if not self.points or self.rescan_requested or now - last_scan > rescan_interval:
                    self.rescan_requested = False
                    self.discover()
                    last_scan = last_param_poll = time.monotonic()
                else:
                    with_params = now - last_param_poll >= param_interval
                    for key in self.pages:
                        if with_params or not key.startswith("menu:"):
                            self.poll_page(key)
                            self._drain_commands(time.monotonic())
                    if with_params:
                        last_param_poll = now
                    self.publish_states()
                self._set_connected(True)
                backoff = 30
                self._drain_commands(time.monotonic() + scan_interval)
            except Exception as err:  # noqa: BLE001 - keep the add-on alive while the HPM is unreachable
                _LOGGER.error("HPM not reachable: %s (retrying in %ds)", err, backoff)
                _LOGGER.debug("Details", exc_info=True)
                self._set_connected(False)
                self.client.sessionid = None
                self._drain_commands(time.monotonic() + backoff)
                backoff = min(backoff * 2, 600)
