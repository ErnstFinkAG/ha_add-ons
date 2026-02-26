import asyncio, json, re, argparse, time, unicodedata
import websockets
import xml.etree.ElementTree as ET
from paho.mqtt import client as mqtt
from datetime import datetime

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def extract_number(value: str):
    m = re.match(r"^\s*([\-+]?\d+(?:[.,]\d+)?)\s*([^\d\s].*)?$", value or "")
    if not m:
        return None, None
    num = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").strip()
    return num, unit

def slug(s: str, keep_slash: bool = False) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"\s+", "_", s)
    if keep_slash:
        s = re.sub(r"[^a-z0-9_\/]+", "", s)
    else:
        s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "x"

class MqttBridge:
    def __init__(self, host, port, username, password, discovery_prefix, state_base_topic):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.discovery = discovery_prefix.rstrip("/")
        self.state_base = state_base_topic.strip("/")

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.on_press_start = None

    def device(self):
        return {
            "identifiers": ["cta_cs19i_bridge"],
            "name": "CTA CS19i Bridge",
            "manufacturer": "CTA",
            "model": "CS19i",
        }

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        print(ts(), "MQTT connected:", reason_code)
        self.client.publish(f"{self.state_base}/status", "online", retain=True)
        self.client.subscribe(f"{self.state_base}/command/#")
        self.pub_button_start()

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = (msg.payload or b"").decode("utf-8", errors="ignore")
            if topic == f"{self.state_base}/command/start_heating" and payload.strip().upper() == "PRESS":
                if self.on_press_start:
                    self.on_press_start()
        except Exception as e:
            print(ts(), "MQTT on_message error:", e)

    def connect(self):
        self.client.will_set(f"{self.state_base}/status", "offline", retain=True)
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def disconnect(self):
        try:
            self.client.publish(f"{self.state_base}/status", "offline", retain=True)
        except Exception:
            pass
        try:
            self.client.loop_stop()
        except Exception:
            pass
        try:
            self.client.disconnect()
        except Exception:
            pass

    def pub_sensor(self, page_title: str, row: dict, page_path: str):
        page_slug = slug(page_path, keep_slash=True).replace("/", "_")
        name_slug = slug(row["name"])
        uniq = f"{self.state_base}_{page_slug}_{name_slug}"
        st_topic = f"{self.state_base}/{slug(page_path, keep_slash=True)}/{name_slug}"
        cfg_topic = f"{self.discovery}/sensor/{uniq}/config"

        num, unit = extract_number(row.get("value", ""))
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
            state_payload = row.get("value", "")
            unit = None

        payload = {
            "name": f"{page_title}: {row.get('name','')}",
            "unique_id": uniq,
            # FIX: deprecated object_id -> default_entity_id
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
            # FIX: deprecated object_id -> default_entity_id
            "default_entity_id": f"button.{uniq}",
            "command_topic": f"{self.state_base}/command/start_heating",
            "payload_press": "PRESS",
            "availability_topic": f"{self.state_base}/status",
            "device": self.device(),
        }
        self.client.publish(cfg_topic, json.dumps(payload), retain=True)

class CTAClient:
    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, int(port), password
        self.ws = None

    async def connect(self):
        uri = f"ws://{self.host}:{self.port}/ws"
        self.ws = await websockets.connect(uri)
        await self.ws.send(self.password)

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def request(self, path: str):
        if not self.ws:
            raise RuntimeError("Not connected")
        msg = json.dumps({"path": path})
        await self.ws.send(msg)
        resp = await self.ws.recv()
        return resp

def parse_table(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    title = root.attrib.get("title") or root.findtext("title") or ""
    path = root.attrib.get("path") or root.findtext("path") or ""

    rows = []
    for r in root.findall(".//row"):
        name = r.attrib.get("name") or r.findtext("name") or ""
        value = r.attrib.get("value") or r.findtext("value") or ""
        name = (name or "").strip()
        value = (value or "").strip()
        if not name and not value:
            continue
        rows.append({"name": name, "value": value})

    return {"title": title, "path": path, "rows": rows}

def diff_rows(prev: dict, curr: dict):
    if not prev:
        return curr.get("rows", [])
    prev_map = {slug(r["name"]): r.get("value") for r in prev.get("rows", [])}
    changed = []
    for r in curr.get("rows", []):
        k = slug(r["name"])
        if prev_map.get(k) != r.get("value"):
            changed.append(r)
    return changed

def normalize_pages(log_pages_value):
    """
    Your stored add-on options currently contain log_pages=True (bool).
    Convert that into a sensible default list.
    """
    if isinstance(log_pages_value, bool):
        return ["/informationen/abschaltungen"] if log_pages_value else []
    if isinstance(log_pages_value, str):
        pages = [p.strip() for p in log_pages_value.split(",") if p.strip()]
        return pages
    return ["/informationen/abschaltungen"]

async def run_bridge(
    controller_host: str,
    controller_port: int,
    controller_password: str,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_user: str,
    mqtt_pass: str,
    poll_interval: int,
    discovery_prefix: str,
    state_base_topic: str,
    log_pages_value,
    log_changes_only: bool,
):
    bridge = MqttBridge(mqtt_host, mqtt_port, mqtt_user, mqtt_pass, discovery_prefix, state_base_topic)
    cta = CTAClient(controller_host, controller_port, controller_password)

    last_pages = {}
    last_press = 0.0

    def do_start_press():
        nonlocal last_press
        now = time.time()
        if now - last_press < 2.0:
            return
        last_press = now
        print(ts(), "Start Heating requested via MQTT button")
        asyncio.create_task(cta.request("/command/start_heating"))

    bridge.on_press_start = do_start_press

    pages = normalize_pages(log_pages_value)

    bridge.connect()
    await cta.connect()

    try:
        while True:
            for page in pages:
                try:
                    resp = await cta.request(page)
                    parsed = parse_table(resp)
                    if not parsed:
                        continue

                    title = parsed.get("title") or page
                    path = parsed.get("path") or page

                    rows_to_publish = parsed.get("rows", [])
                    if log_changes_only:
                        rows_to_publish = diff_rows(last_pages.get(page), parsed)

                    for row in rows_to_publish:
                        bridge.pub_sensor(title, row, path)

                    last_pages[page] = parsed
                except Exception as e:
                    print(ts(), f"Error polling page {page}:", e)

            await asyncio.sleep(int(poll_interval))
    finally:
        await cta.close()
        bridge.disconnect()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hostname", default="10.80.23.11")
    ap.add_argument("--controller-port", default=8214)
    ap.add_argument("--controller-password", default="999990")
    ap.add_argument("--poll-interval", type=int, default=30)
    ap.add_argument("--discovery-prefix", default="homeassistant")
    ap.add_argument("--state-base-topic", default="cta_cs19i")
    ap.add_argument("--mqtt-host", default="core-mosquitto")
    ap.add_argument("--mqtt-port", default=1883)
    ap.add_argument("--mqtt-user", default="")
    ap.add_argument("--mqtt-pass", default="")
    ap.add_argument("--mqtt-username", default="")
    ap.add_argument("--mqtt-password", default="")
    ap.add_argument("--log-pages", default="/informationen/abschaltungen")
    ap.add_argument("--log-changes-only", action="store_true")
    args = ap.parse_args()

    # Prefer mqtt_user/mqtt_pass, but support mqtt_username/mqtt_password too
    mqtt_user = args.mqtt_user or args.mqtt_username or ""
    mqtt_pass = args.mqtt_pass or args.mqtt_password or ""

    asyncio.run(
        run_bridge(
            args.hostname,
            args.controller_port,
            args.controller_password,
            args.mqtt_host,
            args.mqtt_port,
            mqtt_user,
            mqtt_pass,
            args.poll_interval,
            args.discovery_prefix,
            args.state_base_topic,
            args.log_pages,
            args.log_changes_only,
        )
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass