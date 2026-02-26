import asyncio, json, re, argparse, time, unicodedata
import websockets
from websockets.exceptions import ConnectionClosed
import xml.etree.ElementTree as ET
from paho.mqtt import client as mqtt
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Zurich")

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def extract_number(value: str):
    m = re.match(r"^\s*([\-+]?\d+(?:[.,]\d+)?)\s*([^\d\s].*)?$", value or "")
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
    dt = datetime(year, mm, dd, hh, mi, ss, tzinfo=TZ)
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
            {"name": name, "value": value, "id": item_id, "unit": unit, "raw": raw, "div": div, "options": opts}
        )
    return title, rows

def slug(s: str, keep_slash: bool = False) -> str:
    """
    ASCII-only slug for MQTT topic compatibility.
    Keeps only [a-z0-9_], and optionally '/' as separator when keep_slash=True.
    """
    s = (s or "").strip().lower()

    # Common German transliterations
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("ß", "ss"))

    # Normalize + drop remaining non-ascii letters (e.g. ø)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")

    # Convert whitespace/dashes to underscore
    s = re.sub(r"[\s\-]+", "_", s)

    # Allow only safe chars
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
    def __init__(self, host, port, user, pw, discovery_prefix, state_base_topic):
        self.discovery = discovery_prefix.rstrip("/")
        self.state_base = state_base_topic.rstrip("/")
        client_id = f"cs19i_bridge_{int(time.time())}"
        self.client = mqtt.Client(client_id=client_id)
        if user:
            self.client.username_pw_set(user, pw)
        self.on_press_start = None

        def on_connect(c, u, flags, rc):
            print(f"[mqtt] on_connect rc={rc}", flush=True)
            c.publish(f"{self.state_base}/status", "online", retain=True)
            c.subscribe(f"{self.state_base}/command/start_heating")

        def on_message(c, u, msg):
            if msg.topic.endswith("/command/start_heating") and self.on_press_start:
                asyncio.get_event_loop().create_task(self.on_press_start())

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.host, self.port = host, int(port)

    def connect_async(self):
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.publish(f"{self.state_base}/status", "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def device(self):
        return {"identifiers": [self.state_base], "name": "CTA CS19i", "manufacturer": "CTA", "model": "CS19i"}

    def pub_sensor(self, page_title: str, row: dict, page_path: str):
        page_slug = slug(page_path, keep_slash=True).replace("/", "_")
        name_slug = slug(row["name"])
        uniq = f"{self.state_base}_{page_slug}_{name_slug}"

        st_topic = f"{self.state_base}/{slug(page_path, keep_slash=True)}/{name_slug}"
        cfg_topic = f"{self.discovery}/sensor/{uniq}/config"

        num, unit = extract_number(row["value"])
        device_class = None
        state_class = None

        if num is not None and unit:
            state_payload = f"{num}"
            u = (unit or "").strip()
            if u in ("°C", "C", "degC"):
                device_class = "temperature"
            elif u in ("V", "Volt", "volts"):
                device_class = "voltage"
            state_class = "measurement"
        else:
            state_payload = row["value"]
            unit = None

        payload = {
            "name": f"{page_title}: {row['name']}",
            "unique_id": uniq,
            "default_entity_id": f"sensor.{uniq}",
            "state_topic": st_topic,
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class

        self.client.publish(cfg_topic, json.dumps(payload), retain=True)
        self.client.publish(st_topic, state_payload, retain=False)

    def pub_button_start(self):
        uniq = f"{self.state_base}_start_heating"
        cfg_topic = f"{self.discovery}/button/{uniq}/config"
        payload = {
            "name": "CTA Start Heating",
            "unique_id": uniq,
            "default_entity_id": f"button.{uniq}",
            "command_topic": f"{self.state_base}/command/start_heating",
            "payload_press": "PRESS",
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
        }
        self.client.publish(cfg_topic, json.dumps(payload), retain=True)

    def _pub_log_latest(self, kind: str, title: str, rows: list, keep_last: int = 50):
        entries = []
        for r in rows:
            ts_iso = parse_ddmmyy_hhmmss(r.get("name", ""))
            if ts_iso:
                entries.append({"ts": ts_iso, "text": (r.get("value") or "").strip()})

        entries.sort(key=lambda x: x["ts"], reverse=True)
        entries = entries[:keep_last]
        latest_ts = entries[0]["ts"] if entries else ""
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

        self.client.publish(cfg_topic, json.dumps(payload), retain=True)
        self.client.publish(st_topic, latest_ts, retain=False)
        self.client.publish(attr_topic, json.dumps({"latest_text": latest_text, "entries": entries}), retain=False)

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
        headers = [("Origin", f"http://{self.host}")]
        self.ws = await websockets.connect(
            self.uri,
            subprotocols=["Lux_WS"],
            extra_headers=headers,
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
        deadline = asyncio.get_event_loop().time() + overall
        sent_get = False
        last = None
        while asyncio.get_event_loop().time() < deadline:
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

async def run(
    host, port, password,
    mqtt_host, mqtt_port, mqtt_user, mqtt_pass,
    poll_interval, delta_c, discovery_prefix, state_base_topic,
    log_pages, log_changes_only,
    nav_refresh_seconds: int,
    skip_path_prefixes: list[str],
):
    prev = {}
    mqttb = MqttBridge(mqtt_host, mqtt_port, mqtt_user, mqtt_pass, discovery_prefix, state_base_topic)
    mqttb.connect_async()
    mqttb.pub_button_start()

    cta = CTAClient(host, port, password)
    await cta.connect()

    cached_leaves = None
    cached_at = 0.0

    async def refresh_nav(force=False):
        nonlocal cached_leaves, cached_at
        now = time.time()
        if (not force) and cached_leaves and (now - cached_at) < nav_refresh_seconds:
            return cached_leaves

        nav = await cta.get_navigation()
        leaves = list(walk_nav_leaves(nav, []))

        if skip_path_prefixes:
            out = []
            for l in leaves:
                p = l["path"]
                if any(p.startswith(pref) for pref in skip_path_prefixes):
                    continue
                out.append(l)
            leaves = out

        cached_leaves = leaves
        cached_at = now
        print(f"[nav] cached {len(leaves)} leaves (refresh={nav_refresh_seconds}s)", flush=True)
        return leaves

    async def reconnect():
        nonlocal cached_leaves, cached_at
        try:
            await cta.close()
        except Exception:
            pass
        await asyncio.sleep(1)
        await cta.connect()
        cached_leaves = None
        cached_at = 0.0
        await refresh_nav(force=True)

    try:
        await refresh_nav(force=True)

        while True:
            try:
                leaves = await refresh_nav(force=False)
            except Exception as e:
                print(f"[nav] {e}", flush=True)
                if is_ws_close_error(e):
                    await reconnect()
                    continue
                await asyncio.sleep(2)
                continue

            for leaf in leaves:
                try:
                    page = await cta.get_page(leaf["id"], leaf["name"])
                    title, rows = parse_content(page)

                    p = leaf["path"].lower()
                    if p.startswith("informationen/abschaltungen"):
                        mqttb.pub_abschaltungen_latest(title, rows, keep_last=50)
                    elif p.startswith("informationen/fehlerspeicher"):
                        mqttb.pub_fehlerspeicher_latest(title, rows, keep_last=50)
                    else:
                        for r in rows:
                            mqttb.pub_sensor(title, r, leaf["path"])

                    if log_pages:
                        if log_changes_only:
                            changed = []
                            for r in rows:
                                key = (slug(leaf["path"]), r["name"])
                                val = r["value"]
                                if prev.get(key) != val:
                                    changed.append(r)
                                    prev[key] = val
                            if changed:
                                print_table(title, changed, leaf["path"])
                        else:
                            print_table(title, rows, leaf["path"])
                            for r in rows:
                                prev[(slug(leaf["path"]), r["name"])] = r["value"]

                except Exception as e:
                    print(f"[page {leaf['path']}] {e}", flush=True)
                    if is_ws_close_error(e):
                        await reconnect()
                        break

                await asyncio.sleep(0.15)

            await asyncio.sleep(int(poll_interval))

    finally:
        await cta.close()
        mqttb.stop()

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=8214)
    p.add_argument("--password", required=True)
    p.add_argument("--mqtt-host", required=True)
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-user", default="")
    p.add_argument("--mqtt-pass", default="")
    p.add_argument("--poll-interval", type=int, default=30)
    p.add_argument("--demand-delta", type=float, default=5.0)
    p.add_argument("--discovery-prefix", default="homeassistant")
    p.add_argument("--state-base-topic", default="cta_cs19i")
    p.add_argument("--log-pages", action="store_true")
    p.add_argument("--log-changes-only", action="store_true")
    p.add_argument("--nav-refresh-seconds", type=int, default=3600)
    p.add_argument("--skip-path-prefixes", default="Zugang:")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    skip_prefixes = [s.strip() for s in (args.skip_path_prefixes or "").split(",") if s.strip()]
    try:
        asyncio.run(run(
            args.host, args.port, args.password,
            args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_pass,
            args.poll_interval, args.demand_delta, args.discovery_prefix, args.state_base_topic,
            args.log_pages, args.log_changes_only,
            args.nav_refresh_seconds,
            skip_prefixes,
        ))
    except KeyboardInterrupt:
        pass