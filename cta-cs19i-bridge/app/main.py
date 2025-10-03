import asyncio, json, re, time, argparse
import websockets
import xml.etree.ElementTree as ET
from paho.mqtt import client as mqtt
from datetime import datetime

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def extract_number(value: str):
    m = re.match(r"^\s*([\-+]?\d+(?:[.,]\d+)?)\s*([^\d\s].*)?$", value or "")
    if not m: return None, None
    num = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").strip() or None
    return num, unit

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
        rows.append({"name": name, "value": value, "id": item_id, "unit": unit, "raw": raw, "div": div, "options": opts})
    return title, rows

def print_table(title: str, rows: list[dict], page_path: str):
    # widths
    name_w = max([4] + [len(r["name"]) for r in rows])
    val_w  = max([5] + [len(r["value"]) for r in rows])
    id_w   = max([2] + [len(r["id"]) for r in rows])
    print(f"[{ts()}] === {title} ({page_path}) ===", flush=True)
    print(f"{'Name'.ljust(name_w)}  {'Value'.ljust(val_w)}  {'Id'.ljust(id_w)}", flush=True)
    print(f"{'-'*name_w}  {'-'*val_w}  {'-'*id_w}", flush=True)
    for r in rows:
        print(f"{r['name'].ljust(name_w)}  {r['value'].ljust(val_w)}  {r['id'].ljust(id_w)}", flush=True)
    print("", flush=True)

class MqttBridge:
    def __init__(self, host, port, user, pw, prefix):
        self.prefix = prefix.rstrip("/")
        self.client = mqtt.Client(client_id=f"{self.prefix}_bridge")
        if user:
            self.client.username_pw_set(user, pw)
        self.client.will_set(f"{self.prefix}/status", "offline", retain=True)
        self.on_press_start = None

        def on_connect(c, u, flags, rc):
            c.publish(f"{self.prefix}/status", "online", retain=True)
            c.subscribe(f"homeassistant/button/{self.prefix}_start_heating/press")

        def on_message(c, u, msg):
            if msg.topic.endswith("/press") and self.on_press_start:
                asyncio.get_event_loop().create_task(self.on_press_start())

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.host, self.port = host, int(port)

    def connect_async(self):
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.publish(f"{self.prefix}/status", "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def device(self):
        return {"identifiers": [self.prefix], "name": "CTA CS19i", "manufacturer": "CTA", "model": "CS19i"}

    def pub_sensor(self, page, row, page_id):
        uniq_part = re.sub(r'[^a-zA-Z0-9_]', '_', page_id)
        item_part = re.sub(r'[^a-zA-Z0-9_]', '_', row['id'])
        uniq = f"{self.prefix}_{uniq_part}_{item_part}"
        st_topic = f"{self.prefix}/state/{uniq_part}/{item_part}"
        cfg_topic = f"homeassistant/sensor/{uniq}/config"
        num, unit = extract_number(row["value"])
        payload = {"name": f"{page}: {row['name']}", "unique_id": uniq, "state_topic": st_topic, "unit_of_measurement": unit, "device": self.device()}
        self.client.publish(cfg_topic, json.dumps(payload), retain=True)
        self.client.publish(st_topic, row["value"], retain=False)

    def pub_button_start(self):
        uniq = f"{self.prefix}_start_heating"
        cfg_topic = f"homeassistant/button/{uniq}/config"
        payload = {"name": "CTA Start Heating", "unique_id": uniq, "command_topic": f"homeassistant/button/{uniq}/press", "payload_press": "PRESS", "device": self.device()}
        self.client.publish(cfg_topic, json.dumps(payload), retain=True)

class CTAClient:
    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, int(port), password
        self.ws = None
        self.uri = f"ws://{host}:{port}"

    async def connect(self):
        headers = [("Origin", f"http://{self.host}")]
        self.ws = await websockets.connect(self.uri, subprotocols=["Lux_WS"], extra_headers=headers, ping_interval=20, ping_timeout=10)
        await self.send(f"LOGIN;{self.password}")
        await asyncio.sleep(0.2)

    async def close(self):
        if self.ws:
            try: await self.ws.close(code=1000)
            except Exception: pass
            self.ws = None

    async def send(self, text: str):
        if self.ws is None: return
        await self.ws.send(text)

    async def recv_once(self, timeout: float) -> str | None:
        if self.ws is None: return None
        try: return await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError: return None

    async def get_navigation(self, tries=10, timeout=8.0):
        last = None
        for _ in range(tries):
            await self.send("REFRESH")
            txt = await self.recv_once(timeout)
            if txt is None: continue
            last = txt
            if re.search(r"<\s*Navigation(\s|>)", txt, re.I):
                try: return parse_xml(txt)
                except Exception: await asyncio.sleep(0.2)
        raise RuntimeError(f"No Navigation received. Last:\n{last}")

    async def get_page(self, page_id: str, title: str, per_read=4.0, overall=25.0, poll_ms=300):
        deadline = asyncio.get_event_loop().time() + overall
        sent_get = False
        last = None
        while asyncio.get_event_loop().time() < deadline:
            if not sent_get:
                await self.send(f"GET;{page_id}")
                sent_get = True
                await asyncio.sleep(poll_ms/1000)
            await self.send("REFRESH")
            txt = await self.recv_once(per_read)
            if txt is None: continue
            last = txt
            if is_content_of(txt, page_id, title):
                try: return parse_xml(txt)
                except Exception: pass
            await asyncio.sleep(poll_ms/1000)
        raise RuntimeError(f"Timed out waiting for Content of {title} ({page_id}). Last:\n{last}")

async def run(host, port, password, mqtt_host, mqtt_port, mqtt_user, mqtt_pass, poll_interval, delta_c, prefix, log_pages, log_changes_only):
    prev = {}
    mqttb = MqttBridge(mqtt_host, mqtt_port, mqtt_user, mqtt_pass, prefix)
    mqttb.connect_async()
    mqttb.pub_button_start()

    cta = CTAClient(host, port, password)
    await cta.connect()

    async def start_heating():
        try:
            nav = await cta.get_navigation()
            leaves = list(walk_nav_leaves(nav, []))
            p_temp_info = next((l for l in leaves if l["path"].startswith("Informationen/Temperaturen")), None)
            p_temp_set  = next((l for l in leaves if l["path"].startswith("Einstellungen/Temperaturen")), None)
            if not all([p_temp_info, p_temp_set]): return
            info = await cta.get_page(p_temp_info["id"], "Temperaturen")
            _, rows = parse_content(info)
            rueck = next((r for r in rows if r["name"] == "Rücklauf"), None)
            if not rueck: return
            num, _ = extract_number(rueck["value"]); if num is None: return
            target = num + float(delta_c)
            te = await cta.get_page(p_temp_set["id"], "Temperaturen")
            _, rows = parse_content(te)
            minr = next((r for r in rows if r["name"].startswith("Min. Rückl.Solltemp")), None)
            cap  = next((r for r in rows if r["name"] == "Rückl.-Begr."), None)
            if cap:
                cnum, _ = extract_number(cap["value"])
                if cnum is not None and target > cnum: target = cnum
            if minr:
                cur, _ = extract_number(minr["value"]); div = minr.get("div", 1.0) if isinstance(minr.get("div", 1.0), (int, float)) else 1.0
                if (cur is None) or (cur < target - 0.05):
                    raw = int(round(target * div))
                    await cta.send(f"SET;{minr['id']};{raw}")
                    await cta.send("SAVE;1")
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[start_heating] {e}", flush=True)

    mqttb.on_press_start = start_heating

    try:
        while True:
            try:
                nav = await cta.get_navigation()
            except Exception as e:
                print(f"[nav] {e}", flush=True)
                await cta.close(); await asyncio.sleep(2); await cta.connect()
                continue

            leaves = list(walk_nav_leaves(nav, []))
            for leaf in leaves:
                try:
                    page = await cta.get_page(leaf["id"], leaf["name"])
                    title, rows = parse_content(page)
                    for r in rows:
                        mqttb.pub_sensor(title, r, leaf["id"])

                    if log_pages:
                        if log_changes_only:
                            changed = []
                            for r in rows:
                                key = (leaf["id"], r["id"]); val = r["value"]
                                if prev.get(key) != val:
                                    changed.append(r); prev[key] = val
                            if changed:
                                print_table(title, changed, leaf["path"])
                        else:
                            print_table(title, rows, leaf["path"])
                            for r in rows: prev[(leaf["id"], r["id"])] = r["value"]

                except Exception as e:
                    print(f"[page {leaf['path']}] {e}", flush=True)
                await asyncio.sleep(0.15)
            await asyncio.sleep(int(poll_interval))
    finally:
        await cta.close(); mqttb.stop()

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
    p.add_argument("--mqtt-prefix", default="cta_cs19i")
    p.add_argument("--log-pages", action="store_true")
    p.add_argument("--log-changes-only", action="store_true")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run(
            args.host, args.port, args.password,
            args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_pass,
            args.poll_interval, args.demand_delta, args.mqtt_prefix,
            args.log_pages, args.log_changes_only
        ))
    except KeyboardInterrupt:
        pass
