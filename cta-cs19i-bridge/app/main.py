import asyncio, json, os, re, signal, sys, time, unicodedata, urllib.request
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
import xml.etree.ElementTree as ET
from paho.mqtt import client as mqtt
from datetime import datetime
from zoneinfo import ZoneInfo

OPTIONS_PATH = "/data/options.json"

# Supervisor passes the HA timezone as TZ; log timestamps on the controller are local time
try:
    TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Zurich")
except Exception:
    TZ = ZoneInfo("Europe/Zurich")

# consecutive page failures before the controller connection is treated as dead
MAX_PAGE_FAILURES = 3

# unit as shown by the controller -> (device_class, state_class, unit for HA)
UNIT_CLASSES = {
    "°C": ("temperature", "measurement", "°C"),
    "C": ("temperature", "measurement", "°C"),
    "degC": ("temperature", "measurement", "°C"),
    "K": (None, "measurement", "K"),
    "bar": ("pressure", "measurement", "bar"),
    "W": ("power", "measurement", "W"),
    "kW": ("power", "measurement", "kW"),
    "kWh": ("energy", "total_increasing", "kWh"),
    "MWh": ("energy", "total_increasing", "MWh"),
    "V": ("voltage", "measurement", "V"),
    "Volt": ("voltage", "measurement", "V"),
    "%": (None, "measurement", "%"),
    "h": ("duration", "total_increasing", "h"),
    "min": ("duration", "measurement", "min"),
    "s": ("duration", "measurement", "s"),
    "Hz": ("frequency", "measurement", "Hz"),
    "l/h": ("volume_flow_rate", "measurement", "L/h"),
    "L/h": ("volume_flow_rate", "measurement", "L/h"),
    "rpm": (None, "measurement", "rpm"),
}

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def extract_number(value: str):
    # a unit containing digits ("00:12:34", "1h 23m") is not a plain number with a unit
    m = re.match(r"^\s*([\-+]?\d+(?:[.,]\d+)?)\s*([^\d\s][^\d]*)?$", value or "")
    if not m:
        return None, None
    num = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").strip() or None
    return num, unit

def parse_ddmmyy_hhmmss(s: str):
    if not s:
        return None
    m = re.match(r"^\s*(\d{2})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s*$", s)
    if not m:
        return None
    dd, mm, yy, hh, mi, ss = map(int, m.groups())
    year = 2000 + yy
    try:
        dt = datetime(year, mm, dd, hh, mi, ss, tzinfo=TZ)
    except ValueError:
        return None
    return dt.isoformat()

def parse_xml(xml_text: str) -> ET.Element:
    return ET.fromstring(xml_text)

def is_content_of(xml_text: str, page_id: str, page_title: str) -> bool:
    if re.search(r"<\s*Content(\s|>)", xml_text or "", re.I):
        if page_id and re.search(rf"id\s*=\s*['\"]{re.escape(page_id)}['\"]", xml_text):
            return True
        try:
            root = parse_xml(xml_text)
            if (root.tag.lower() == "content") and root.findtext("name") == page_title:
                return True
        except Exception:
            pass
    return False

def walk_nav_leaves(elem: ET.Element, ancestors):
    for it in elem.findall("./item"):
        name_el = it.find("name")
        if name_el is None:
            continue
        name = name_el.text or ""
        path = ancestors + [name]
        if it.find("./item") is not None:
            for leaf in walk_nav_leaves(it, path):
                yield leaf
        else:
            yield {"id": it.get("id") or "", "name": name, "path": "/".join(path)}

def parse_content(root: ET.Element):
    title = root.findtext("name") or "Content"
    rows = []
    for it in root.findall("item"):
        name = it.findtext("name") or ""
        value = it.findtext("value") or ""
        item_id = it.get("id") or ""
        unit = it.findtext("unit")
        raw = it.findtext("raw")
        div_txt = it.findtext("div")
        try:
            div = float(div_txt) if div_txt is not None else 1.0
        except Exception:
            div = 1.0
        opts = []
        for opt in it.findall("option"):
            opts.append({"text": (opt.text or ""), "val": opt.get("value")})
        rows.append(
            {"name": name, "value": value, "id": item_id, "unit": unit, "raw": raw, "div": div, "options": opts,
             "min": it.findtext("min"), "max": it.findtext("max"), "step": it.findtext("step")}
        )
    return title, rows

def control_kind(row: dict):
    """'select' or 'number' if the controller lets this row be changed, else None."""
    if row.get("raw") is None:
        return None
    if row["options"]:
        return "select"
    if row.get("min") is not None and row.get("max") is not None:
        return "number"
    return None

def raw_scale(row: dict) -> int:
    """Factor between the raw value and the shown value (650 -> 65.0 °C is 10)."""
    num, _ = extract_number(row["value"])
    try:
        raw = float(row["raw"])
    except (TypeError, ValueError):
        raw = 0.0
    if num and raw:
        return max(1, round(abs(raw / num)))
    return 10 if re.search(r"\d[.,]\d", row["value"] or "") else 1

def hvac_for(option_text: str):
    """HA hvac mode for a heating operating mode, or None if it is better shown as a preset."""
    t = (option_text or "").strip().lower()
    if t.startswith("auto"):
        return "auto"
    if "party" in t:
        return "heat"
    if t in ("aus", "off"):
        return "off"
    return None

def slug(s: str, keep_slash: bool = False) -> str:
    s = (s or "").strip().lower()
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("ß", "ss"))
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[\s\-]+", "_", s)
    if keep_slash:
        s = re.sub(r"[^a-z0-9_/]+", "_", s)
        s = re.sub(r"/+", "/", s)
    else:
        s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "x"

def print_table(title: str, rows, page_path: str):
    name_w = max([4] + [len(r["name"]) for r in rows])
    val_w  = max([5] + [len(r["value"]) for r in rows])
    id_w   = max([2] + [len(r["id"]) for r in rows])
    print(f"[{ts()}] === {title} ({page_path}) ===", flush=True)
    print(f"{'Name'.ljust(name_w)}  {'Value'.ljust(val_w)}  {'Id'.ljust(id_w)}", flush=True)
    print(f"{'-'*name_w}  {'-'*val_w}  {'-'*id_w}", flush=True)
    for r in rows:
        print(f"{r['name'].ljust(name_w)}  {r['value'].ljust(val_w)}  {r['id'].ljust(id_w)}", flush=True)
    print("", flush=True)

def is_ws_close_error(e: Exception) -> bool:
    msg = str(e) or ""
    return (
        isinstance(e, ConnectionClosed)
        or "no close frame received" in msg.lower()
        or "connection closed" in msg.lower()
        or "ws closed" in msg.lower()
    )

class MqttBridge:
    def __init__(self, host, port, user, pw, discovery_prefix, state_base_topic, cleanup_discovery: bool):
        self.discovery = discovery_prefix.rstrip("/")
        self.state_base = state_base_topic.rstrip("/")
        self.cleanup_discovery = cleanup_discovery

        # discovery configs already sent (topic -> payload); cleared when HA restarts
        self.announced = {}
        # numeric or text, fixed per entity on first publish so its config never flips
        self.numeric = {}
        # every config topic this process has published, and retained ones of ours found on the broker
        self.published_configs = set()
        self.found_configs = set()
        self.cleanup_done = False
        # set by run(): called from the MQTT thread with (unique_id, payload) for HA commands
        self.on_command = None

        client_id = f"cs19i_bridge_{int(time.time())}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if user:
            self.client.username_pw_set(user, pw)
        # Broker marks us offline if the add-on dies without a clean disconnect
        self.client.will_set(f"{self.state_base}/status", "offline", retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        def on_connect(c, u, flags, reason_code, properties):
            if reason_code.is_failure:
                print(f"[mqtt] connect refused: {reason_code}", flush=True)
                return
            print("[mqtt] connected", flush=True)
            c.publish(f"{self.state_base}/status", "online", retain=True)
            # HA announces "online" here when it (re)starts; configs are then sent again
            c.subscribe(f"{self.discovery}/status")
            c.subscribe(f"{self.state_base}/set/+")
            if self.cleanup_discovery and not self.cleanup_done:
                c.subscribe(f"{self.discovery}/+/+/config")
            # a reconnect may follow a broker restart without persistence
            self.announced.clear()
            # remove the old "Start Heating" button, it was never functional
            c.publish(f"{self.discovery}/button/{self.state_base}_start_heating/config", b"", retain=True)

        def on_message(c, u, msg):
            if msg.topic.startswith(f"{self.state_base}/set/"):
                if self.on_command:
                    self.on_command(msg.topic.rsplit("/", 1)[-1], msg.payload.decode("utf-8", "replace"))
                return
            if msg.topic == f"{self.discovery}/status":
                if msg.payload == b"online":
                    print("[mqtt] Home Assistant restarted, re-sending discovery", flush=True)
                    self.announced.clear()
                return
            if msg.topic.endswith("/config") and msg.retain and msg.payload:
                object_id = msg.topic.split("/")[-2]
                if object_id.startswith(f"{self.state_base}_"):
                    self.found_configs.add(msg.topic)

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.host, self.port = host, int(port)

    def connect_async(self):
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            info = self.client.publish(f"{self.state_base}/status", "offline", retain=True)
            info.wait_for_publish(timeout=2)
        except Exception:
            pass
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            pass

    def device(self):
        return {"identifiers": [self.state_base], "name": "CTA CS19i", "manufacturer": "CTA", "model": "CS19i"}

    def _announce(self, cfg_topic: str, payload: dict):
        data = json.dumps(payload)
        if self.announced.get(cfg_topic) == data:
            return
        self.client.publish(cfg_topic, data, retain=True)
        self.announced[cfg_topic] = data
        self.published_configs.add(cfg_topic)

    # ---------- discovery cleanup ----------

    def cleanup_stale(self):
        """Remove retained configs of ours that the current controller pages no longer produce.

        Only called after a complete cycle, so every current entity is in self.published_configs.
        """
        if self.cleanup_done:
            return
        self.cleanup_done = True
        self.client.unsubscribe(f"{self.discovery}/+/+/config")
        stale = sorted(t for t in self.found_configs if t not in self.published_configs)
        for t in stale:
            self.client.publish(t, b"", retain=True)
        print(f"[mqtt] cleanup: removed {len(stale)} stale discovery config(s)", flush=True)
        self.found_configs.clear()

    # ---------- publishing ----------

    def pub_sensor(self, page_title: str, row: dict, page_path: str, suffix: str = ""):
        page_slug = slug(page_path, keep_slash=True).replace("/", "_")
        name_slug = slug(row["name"]) + suffix
        uniq = f"{self.state_base}_{page_slug}_{name_slug}"

        st_topic = f"{self.state_base}/{slug(page_path, keep_slash=True)}/{name_slug}"
        cfg_topic = f"{self.discovery}/sensor/{uniq}/config"

        num, unit = extract_number(row["value"])
        if uniq not in self.numeric:
            self.numeric[uniq] = unit if (num is not None and unit) else None
        ha_unit_src = self.numeric[uniq]

        payload = {
            "name": f"{page_title}: {row['name']}",
            "unique_id": uniq,
            "default_entity_id": f"sensor.{uniq}",
            "state_topic": st_topic,
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
        }

        if ha_unit_src:
            device_class, state_class, ha_unit = UNIT_CLASSES.get(ha_unit_src, (None, "measurement", ha_unit_src))
            payload["unit_of_measurement"] = ha_unit
            payload["state_class"] = state_class
            if device_class:
                payload["device_class"] = device_class
            # "None" makes HA show unknown instead of rejecting a non-numeric value
            state_payload = f"{num}" if (num is not None and unit == ha_unit_src) else "None"
        else:
            state_payload = row["value"]

        self._announce(cfg_topic, payload)
        self.client.publish(st_topic, state_payload, retain=True)

    def pub_control(self, page_title: str, row: dict, page_path: str, suffix: str = ""):
        """Publish a changeable setting as an HA select or number. Returns its spec, or None."""
        kind = control_kind(row)
        if not kind:
            return None
        page_slug = slug(page_path, keep_slash=True).replace("/", "_")
        name_slug = slug(row["name"]) + suffix
        uniq = f"{self.state_base}_{page_slug}_{name_slug}"
        st_topic = f"{self.state_base}/{slug(page_path, keep_slash=True)}/{name_slug}"
        cfg_topic = f"{self.discovery}/{kind}/{uniq}/config"

        payload = {
            "name": f"{page_title}: {row['name']}",
            "unique_id": uniq,
            "default_entity_id": f"{kind}.{uniq}",
            "state_topic": st_topic,
            "command_topic": f"{self.state_base}/set/{uniq}",
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
        }
        spec = {"kind": kind, "uniq": uniq}

        if kind == "select":
            options = {o["text"]: o["val"] for o in row["options"] if o["val"] is not None}
            payload["options"] = list(options)
            state = row["value"] if row["value"] in options else "None"
            spec["options"] = options
        else:
            scale = raw_scale(row)
            num, unit = extract_number(row["value"])
            payload["min"] = float(row["min"]) / scale
            payload["max"] = float(row["max"]) / scale
            try:
                payload["step"] = float(row["step"] or 1) / scale
            except ValueError:
                payload["step"] = 1 / scale
            payload["mode"] = "box"
            if unit:
                device_class, _, ha_unit = UNIT_CLASSES.get(unit, (None, None, unit))
                payload["unit_of_measurement"] = ha_unit
                if device_class == "temperature":
                    payload["device_class"] = "temperature"
            state = f"{float(row['raw']) / scale}"
            spec.update(scale=scale, min=float(row["min"]), max=float(row["max"]))

        self._announce(cfg_topic, payload)
        self.client.publish(st_topic, state, retain=True)
        return spec

    def pub_climate(self, mode, target, current):
        """Thermostat entity from the heating mode setting, the temperature setting and a measured temperature."""
        uniq = f"{self.state_base}_heizung"
        base = f"{self.state_base}/climate"
        payload = {
            "name": "Heizung",
            "unique_id": uniq,
            "default_entity_id": f"climate.{uniq}",
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
            "temperature_unit": "C",
            "precision": 0.1,
        }
        states = {}
        if mode:
            options = list(mode["options"])
            modes = [h for h in (hvac_for(o) for o in options) if h]
            presets = [o for o in options if not hvac_for(o)]
            payload["modes"] = list(dict.fromkeys(modes)) or ["heat"]
            payload["mode_state_topic"] = f"{base}/mode"
            payload["mode_command_topic"] = f"{self.state_base}/set/climate_mode"
            value = mode["row"]["value"]
            hvac = hvac_for(value)
            if not hvac:
                hvac = "off" if "ferien" in value.lower() else "heat"
            states[f"{base}/mode"] = hvac
            if presets:
                payload["preset_modes"] = presets
                payload["preset_mode_state_topic"] = f"{base}/preset"
                payload["preset_mode_command_topic"] = f"{self.state_base}/set/climate_preset"
                states[f"{base}/preset"] = value if value in presets else "none"
        else:
            payload["modes"] = ["heat"]
        if target:
            scale = target["scale"]
            payload["temperature_state_topic"] = f"{base}/target"
            payload["temperature_command_topic"] = f"{self.state_base}/set/climate_target"
            payload["min_temp"] = target["min"] / scale
            payload["max_temp"] = target["max"] / scale
            try:
                payload["temp_step"] = float(target["row"]["step"] or 1) / scale
            except ValueError:
                payload["temp_step"] = 1 / scale
            states[f"{base}/target"] = f"{float(target['row']['raw']) / scale}"
        if current:
            num, _ = extract_number(current["row"]["value"])
            payload["current_temperature_topic"] = f"{base}/current"
            states[f"{base}/current"] = f"{num}" if num is not None else "None"

        self._announce(f"{self.discovery}/climate/{uniq}/config", payload)
        for topic, value in states.items():
            self.client.publish(topic, value, retain=True)

    def _pub_log_latest(self, kind: str, title: str, rows: list, keep_last: int = 50):
        entries = []
        for r in rows:
            ts_iso = parse_ddmmyy_hhmmss(r.get("name", ""))
            if ts_iso:
                entries.append({"ts": ts_iso, "text": (r.get("value") or "").strip()})

        entries.sort(key=lambda x: x["ts"], reverse=True)
        entries = entries[:keep_last]
        latest_ts = entries[0]["ts"] if entries else "None"
        latest_text = entries[0]["text"] if entries else ""

        uniq = f"{self.state_base}_informationen_{kind}_latest"
        cfg_topic = f"{self.discovery}/sensor/{uniq}/config"
        st_topic = f"{self.state_base}/informationen/{kind}/latest"
        attr_topic = f"{self.state_base}/informationen/{kind}/attributes"

        payload = {
            "name": f"{title}: Latest",
            "unique_id": uniq,
            "default_entity_id": f"sensor.{uniq}",
            "state_topic": st_topic,
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
            "device_class": "timestamp",
            "json_attributes_topic": attr_topic,
        }

        self._announce(cfg_topic, payload)
        self.client.publish(st_topic, latest_ts, retain=True)
        self.client.publish(attr_topic, json.dumps({"latest_text": latest_text, "entries": entries}), retain=True)

    def pub_abschaltungen_latest(self, title: str, rows: list, keep_last: int = 50):
        self._pub_log_latest("abschaltungen", title, rows, keep_last=keep_last)

    def pub_fehlerspeicher_latest(self, title: str, rows: list, keep_last: int = 50):
        self._pub_log_latest("fehlerspeicher", title, rows, keep_last=keep_last)

class CTAClient:
    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, int(port), password
        self.ws = None
        self.uri = f"ws://{host}:{port}"

    async def connect(self):
        self.ws = await ws_connect(
            self.uri,
            subprotocols=["Lux_WS"],
            origin=f"http://{self.host}",
            ping_interval=None,
            ping_timeout=None,
            close_timeout=1,
            max_size=None,
        )
        await self.send(f"LOGIN;{self.password}")
        await asyncio.sleep(0.2)

    async def close(self):
        if self.ws:
            try:
                await self.ws.close(code=1000)
            except Exception:
                pass
            self.ws = None

    async def send(self, text: str):
        if self.ws is None:
            raise RuntimeError("WS not connected")
        try:
            await self.ws.send(text)
        except ConnectionClosed as e:
            self.ws = None
            raise RuntimeError(f"WS closed: {e}") from e

    async def recv_once(self, timeout: float):
        if self.ws is None:
            raise RuntimeError("WS not connected")
        try:
            return await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except ConnectionClosed as e:
            self.ws = None
            raise RuntimeError(f"WS closed: {e}") from e

    async def get_navigation(self, tries=15, timeout=8.0):
        last = None
        for _ in range(tries):
            await self.send("REFRESH")
            txt = await self.recv_once(timeout)
            if txt is None:
                continue
            last = txt
            if re.search(r"<\s*Navigation(\s|>)", txt, re.I):
                return parse_xml(txt)
            await asyncio.sleep(0.2)
        raise RuntimeError(f"No Navigation received. Last:\n{last}")

    async def get_page(self, page_id: str, title: str, per_read=4.0, overall=25.0, poll_ms=300):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + overall
        sent_get = False
        last = None
        while loop.time() < deadline:
            if not sent_get:
                await self.send(f"GET;{page_id}")
                sent_get = True
                await asyncio.sleep(poll_ms / 1000)

            await self.send("REFRESH")
            txt = await self.recv_once(per_read)
            if txt is None:
                continue
            last = txt
            if is_content_of(txt, page_id, title):
                return parse_xml(txt)
            await asyncio.sleep(poll_ms / 1000)

        raise RuntimeError(f"Timed out waiting for Content of {title} ({page_id}). Last:\n{last}")

def supervisor_mqtt():
    """MQTT service (Mosquitto add-on) credentials from the Supervisor, if any."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("data") or None
    except Exception as e:
        print(f"[mqtt] no MQTT service from the Supervisor ({e})", flush=True)
        return None

def split_list(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]

async def run(opts: dict, mqtt_cfg: dict):
    # Supervisor stops the add-on with SIGTERM: cancel so the finally block runs
    main_task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main_task.cancel)

    host, port = opts["hostname"], int(opts.get("controller_port", 8214))
    poll_interval = int(opts.get("poll_interval", 30))
    nav_refresh_seconds = int(opts.get("nav_refresh_seconds", 3600))
    log_pages = bool(opts.get("log_pages", False))
    log_changes_only = bool(opts.get("log_changes_only", True))
    skip_path_prefixes = split_list(opts.get("skip_path_prefixes", "Zugang:"))
    include_path_prefixes = split_list(opts.get("include_path_prefixes", ""))
    writable_path_prefixes = split_list(opts.get("writable_path_prefixes", ""))
    climate_enabled = bool(opts.get("climate", True))
    climate_cfg = {
        "mode": (opts.get("climate_mode_setting") or "").strip(),
        "target": (opts.get("climate_temperature_setting") or "").strip(),
        "current": (opts.get("climate_current_temperature") or "").strip(),
    }

    prev = {}
    mqttb = MqttBridge(mqtt_cfg["host"], mqtt_cfg["port"], mqtt_cfg["username"], mqtt_cfg["password"],
                       opts.get("discovery_prefix", "homeassistant"), opts.get("state_base_topic", "cta_cs19i"),
                       cleanup_discovery=bool(opts.get("cleanup_discovery", True)))
    loop = asyncio.get_running_loop()
    commands: asyncio.Queue = asyncio.Queue()
    mqttb.on_command = lambda uniq, payload: loop.call_soon_threadsafe(commands.put_nowait, (uniq, payload))
    mqttb.connect_async()

    cta = CTAClient(host, port, opts.get("controller_password", ""))

    # unique_id -> {"kind", "leaf", "name", "suffix", ...} for settings HA may change
    controls = {}
    logged_controls = set()
    hinted_pages = set()
    # firmware differs in how a setting is addressed: "SET;set_<id>;<raw>" or "SET;<id>;<raw>"
    set_prefixes = ["set_", ""]

    # parts of the thermostat entity: {"key", "leaf", "name", "suffix", "row", "prio", ...}
    climate = {"mode": None, "target": None, "current": None}

    def climate_priority(part, key, row):
        """Lower is better; None if the row can't serve as this part. Explicit options win."""
        name = row["name"].lower()
        path = key.lower()
        if climate_cfg[part]:
            return 0 if key == climate_cfg[part] else None
        kind = control_kind(row)
        if part == "mode":
            if kind == "select" and name.startswith("heiz") and any(hvac_for(o["text"]) == "auto" for o in row["options"]):
                return 1
        elif part == "target":
            if kind == "number" and ("+-" in name or "±" in name) and "warmw" not in path and "ww" not in name:
                return 1
        elif part == "current":
            num, _ = extract_number(row["value"])
            if num is None:
                return None
            if name.startswith("raum"):
                return 1
            if name.startswith("rücklauf") and "soll" not in name:
                return 2
        return None

    def track_climate(leaf, rows, suffixes):
        for r, sfx in zip(rows, suffixes):
            key = f"{leaf['path']}/{r['name']}"
            for part in climate:
                cur = climate[part]
                if cur and cur["key"] == key and cur["suffix"] == sfx:
                    cur["row"], cur["leaf"] = r, leaf
                    continue
                prio = climate_priority(part, key, r)
                if prio is None or (cur and cur["prio"] <= prio):
                    continue
                info = {"key": key, "leaf": leaf, "name": r["name"], "suffix": sfx, "row": r, "prio": prio}
                if part == "mode":
                    info.update(kind="select", options={o["text"]: o["val"] for o in r["options"] if o["val"] is not None})
                elif part == "target":
                    info.update(kind="number", scale=raw_scale(r), min=float(r["min"]), max=float(r["max"]))
                climate[part] = info
                print(f"[climate] using '{key}' as {part}", flush=True)
                if part in ("mode", "target"):
                    controls[f"climate_{part}"] = info
                    if part == "mode":
                        controls["climate_preset"] = info

    def publish_climate():
        if climate_enabled and (climate["mode"] or climate["target"]):
            mqttb.pub_climate(climate["mode"], climate["target"], climate["current"])

    cached_leaves = None
    cached_at = 0.0

    async def refresh_nav(force=False):
        nonlocal cached_leaves, cached_at
        now = time.time()
        if (not force) and cached_leaves and (now - cached_at) < nav_refresh_seconds:
            return cached_leaves

        nav = await cta.get_navigation()
        leaves = list(walk_nav_leaves(nav, []))
        if include_path_prefixes:
            leaves = [l for l in leaves if any(l["path"].startswith(p) for p in include_path_prefixes)]
        if skip_path_prefixes:
            leaves = [l for l in leaves if not any(l["path"].startswith(p) for p in skip_path_prefixes)]

        cached_leaves = leaves
        cached_at = now
        print(f"[nav] cached {len(leaves)} leaves (refresh={nav_refresh_seconds}s)", flush=True)
        return leaves

    async def reconnect():
        # retry until the controller answers; it may be off or rebooting
        nonlocal cached_leaves, cached_at
        delay = 5
        while True:
            await cta.close()
            cached_leaves = None
            cached_at = 0.0
            try:
                await cta.connect()
                await refresh_nav(force=True)
                print(f"[cta] connected to {host}:{port}", flush=True)
                return
            except Exception as e:
                print(f"[cta] connect failed: {e}; retrying in {delay}s", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)

    def row_suffixes(rows):
        # rows with the same name on one page get _2, _3, ... so they don't share an entity
        seen, suffixes = {}, []
        for r in rows:
            n = seen.get(r["name"], 0) + 1
            seen[r["name"]] = n
            suffixes.append("" if n == 1 else f"_{n}")
        return suffixes

    def publish_page(leaf, page):
        title, rows = parse_content(page)
        suffixes = row_suffixes(rows)
        writable = any(leaf["path"].startswith(w) for w in writable_path_prefixes)

        p = leaf["path"].lower()
        if p.startswith("informationen/abschaltungen"):
            mqttb.pub_abschaltungen_latest(title, rows, keep_last=50)
        elif p.startswith("informationen/fehlerspeicher"):
            mqttb.pub_fehlerspeicher_latest(title, rows, keep_last=50)
        else:
            for r, sfx in zip(rows, suffixes):
                spec = mqttb.pub_control(title, r, leaf["path"], suffix=sfx) if writable else None
                if spec:
                    controls[spec["uniq"]] = dict(spec, leaf=leaf, name=r["name"], suffix=sfx)
                    if spec["uniq"] not in logged_controls:
                        logged_controls.add(spec["uniq"])
                        print(f"[control] {leaf['path']}/{r['name']}: {spec['kind']} raw={r['raw']} value={r['value']!r} "
                              f"min={r['min']} max={r['max']} step={r['step']} "
                              f"options={[(o['val'], o['text']) for o in r['options']]}", flush=True)
                else:
                    mqttb.pub_sensor(title, r, leaf["path"], suffix=sfx)
            if climate_enabled:
                track_climate(leaf, rows, suffixes)
            if not writable and leaf["path"] not in hinted_pages and any(control_kind(r) for r in rows):
                hinted_pages.add(leaf["path"])
                print(f"[settings] '{leaf['path']}' has changeable values; add it to writable_path_prefixes "
                      f"to control them from Home Assistant", flush=True)
        return title, rows, suffixes

    async def apply_command(uniq, payload):
        ctl = controls.get(uniq)
        if not ctl:
            print(f"[write] ignored command for unknown setting {uniq}", flush=True)
            return
        if uniq == "climate_mode":
            # HA hvac mode -> controller option text
            payload = next((o for o in ctl["options"] if hvac_for(o) == payload), payload)
        elif uniq == "climate_preset" and payload == "none":
            payload = next((o for o in ctl["options"] if hvac_for(o) == "auto"), payload)
        if ctl["kind"] == "select":
            raw = ctl["options"].get(payload)
            if raw is None:
                print(f"[write] {uniq}: {payload!r} is not one of {list(ctl['options'])}", flush=True)
                return
        else:
            try:
                raw_num = round(float(payload.replace(",", ".")) * ctl["scale"])
            except ValueError:
                print(f"[write] {uniq}: {payload!r} is not a number", flush=True)
                return
            if not (ctl["min"] <= raw_num <= ctl["max"]):
                print(f"[write] {uniq}: {payload} is outside the allowed range", flush=True)
                return
            raw = str(raw_num)

        leaf = ctl["leaf"]

        def find_row(page):
            _, rows = parse_content(page)
            for r, sfx in zip(rows, row_suffixes(rows)):
                if r["name"] == ctl["name"] and sfx == ctl["suffix"]:
                    return r
            return None

        # item ids change between sessions, so take the id from a fresh read of the page
        row = find_row(await cta.get_page(leaf["id"], leaf["name"]))
        if row is None:
            print(f"[write] {leaf['path']}/{ctl['name']}: setting not found on the page any more", flush=True)
            return
        if row["raw"] == raw:
            print(f"[write] {leaf['path']}/{ctl['name']}: already {raw}", flush=True)
            return

        for prefix in list(set_prefixes):
            await cta.send(f"SET;{prefix}{row['id']};{raw}")
            await cta.send("SAVE;1")
            await asyncio.sleep(1)
            page = await cta.get_page(leaf["id"], leaf["name"])
            publish_page(leaf, page)
            publish_climate()
            row = find_row(page) or row
            if row["raw"] == raw:
                # remember the form this firmware accepts
                set_prefixes.sort(key=lambda x: x != prefix)
                print(f"[write] {leaf['path']}/{ctl['name']} = {payload} (raw {raw})", flush=True)
                return
        print(f"[write] {leaf['path']}/{ctl['name']}: controller did not take {payload} (raw {raw}); "
              f"the controller password may lack permission for this setting", flush=True)

    async def run_commands(wait: float = 0.0):
        """Apply queued HA commands; with wait > 0, keep handling them for that many seconds."""
        deadline = loop.time() + wait
        while True:
            try:
                remaining = deadline - loop.time()
                if remaining > 0:
                    uniq, payload = await asyncio.wait_for(commands.get(), remaining)
                else:
                    uniq, payload = commands.get_nowait()
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                return
            try:
                await apply_command(uniq, payload)
            except Exception as e:
                print(f"[write] {uniq}: {e}", flush=True)

    try:
        await reconnect()

        failures = 0
        while True:
            try:
                leaves = await refresh_nav(force=False)
            except Exception as e:
                print(f"[nav] {e}", flush=True)
                await reconnect()
                continue

            cycle_complete = True
            for leaf in leaves:
                try:
                    await run_commands()
                    page = await cta.get_page(leaf["id"], leaf["name"])
                    failures = 0
                    title, rows, suffixes = publish_page(leaf, page)

                    if log_pages:
                        if log_changes_only:
                            changed = []
                            for r, sfx in zip(rows, suffixes):
                                key = (slug(leaf["path"]), r["name"] + sfx)
                                val = r["value"]
                                if prev.get(key) != val:
                                    changed.append(r)
                                    prev[key] = val
                            if changed:
                                print_table(title, changed, leaf["path"])
                        else:
                            print_table(title, rows, leaf["path"])
                            for r, sfx in zip(rows, suffixes):
                                prev[(slug(leaf["path"]), r["name"] + sfx)] = r["value"]

                except Exception as e:
                    print(f"[page {leaf['path']}] {e}", flush=True)
                    cycle_complete = False
                    failures += 1
                    # a connection that died without a close frame only shows up as timeouts
                    if is_ws_close_error(e) or failures >= MAX_PAGE_FAILURES:
                        await reconnect()
                        failures = 0
                        break

                await asyncio.sleep(0.15)

            publish_climate()

            # only clean up after a full cycle, so a missing page never deletes its entities
            if cycle_complete and mqttb.cleanup_discovery and not mqttb.cleanup_done and mqttb.client.is_connected():
                mqttb.cleanup_stale()

            await run_commands(wait=poll_interval)

    except asyncio.CancelledError:
        print("[main] stopping", flush=True)
    finally:
        await cta.close()
        mqttb.stop()

def mqtt_settings(opts: dict) -> dict:
    cfg = {
        "host": opts.get("mqtt_host") or "",
        "port": int(opts.get("mqtt_port") or 1883),
        "username": opts.get("mqtt_username") or "",
        "password": opts.get("mqtt_password") or "",
    }
    if not cfg["username"]:
        svc = supervisor_mqtt()
        if svc:
            print("[mqtt] using MQTT credentials from the Supervisor", flush=True)
            cfg["host"] = svc.get("host") or cfg["host"]
            cfg["port"] = int(svc.get("port") or cfg["port"])
            cfg["username"] = svc.get("username", "")
            cfg["password"] = svc.get("password", "")
    if not cfg["host"]:
        cfg["host"] = "core-mosquitto"
    return cfg

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else OPTIONS_PATH
    with open(path, "r", encoding="utf-8") as f:
        opts = json.load(f)
    mqtt_cfg = mqtt_settings(opts)
    print(f"[startup] Controller -> {opts['hostname']}:{opts.get('controller_port', 8214)}", flush=True)
    print(f"[startup] MQTT -> host={mqtt_cfg['host']} port={mqtt_cfg['port']} user={mqtt_cfg['username'] or '-'}", flush=True)
    asyncio.run(run(opts, mqtt_cfg))
