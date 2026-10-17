"""End-to-end test: fake HPM + MQTT broker on 127.0.0.1:18830 + the bridge.

python3 tests/test_end_to_end.py   (from the aquarea_hpm folder; needs an MQTT broker on 127.0.0.1:18830)
"""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fake_hpm  # noqa: E402
from hpm2mqtt.bridge import Bridge  # noqa: E402

server = fake_hpm.serve(18099)
threading.Thread(target=server.serve_forever, daemon=True).start()

tmp = Path(tempfile.mkdtemp())
options = {"host": "127.0.0.1:18099", "device_name": "Test HPM", "scan_interval": 10, "parameter_interval": 10, "request_delay": 0,
           "dump_html": True, "dump_dir": str(tmp / "dump"), "state_file": str(tmp / "entities.json"),
           "session_file": str(tmp / "session"), "access_code": "4321",
           }
bridge = Bridge(options, {"host": "127.0.0.1", "port": 18830})
threading.Thread(target=bridge.run, daemon=True).start()

seen = {}
watcher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
watcher.on_message = lambda c, u, m: seen.__setitem__(m.topic, m.payload.decode())
watcher.connect("127.0.0.1", 18830)
watcher.subscribe("homeassistant/#")
watcher.subscribe("hpm/#")
watcher.loop_start()


def wait(cond, what, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {what}; seen={json.dumps(seen, indent=1, ensure_ascii=False)[:3000]}")


base = "hpm/test_hpm"
wait(lambda: len([t for t in seen if t.endswith("/config")]) >= 10, "discovery")
configs = {t: json.loads(p) for t, p in seen.items() if t.endswith("/config") and p}
for t in sorted(configs):
    c = configs[t]
    print(f"{t.split('/')[1]:13} {c['name']:40} {c.get('unit_of_measurement', ''):3} "
          f"{c.get('options', '') or (c.get('min'), c.get('max')) if 'command_topic' in c else ''}")

assert "homeassistant/sensor/test_hpm/dp_37_6_4_7/config" in configs
num = configs["homeassistant/number/test_hpm/dp_55_6_4_3/config"]
assert (num["min"], num["max"], num["step"]) == (35.0, 60.0, 0.1), num
sel = configs["homeassistant/select/test_hpm/dp_60_1_1_1/config"]
assert sel["options"] == ["Aus", "Auto", "Heizen", "Kühlen"], sel
menu_cfg = {t.split("/")[3]: (t.split("/")[1], c) for t, c in configs.items() if "dp_menu_" in t}
print(sorted((k, comp, c["name"]) for k, (comp, c) in menu_cfg.items()))
# menu_values=useful: status, Istwerte, hardware info and the recommended settings only
assert set(menu_cfg) == {"dp_menu_1_1_1_1_1", "dp_menu_1_1_1_1_2", "dp_menu_1_4_1_1", "dp_menu_1_4_1_2",
                         "dp_menu_2_1_1_1_1", "dp_menu_2_1_1_1_2", "dp_menu_2_1_1_2_1", "dp_menu_2_1_1_2_2"}, sorted(menu_cfg)
assert menu_cfg["dp_menu_1_4_1_1"][1]["name"] == "Hardware Reglertemp"
assert menu_cfg["dp_menu_1_4_1_1"][1]["unit_of_measurement"] == "°C"
assert menu_cfg["dp_menu_1_4_1_1"][1]["entity_category"] == "diagnostic"
setp = menu_cfg["dp_menu_2_1_1_2_1"]
assert setp[0] == "number" and (setp[1]["min"], setp[1]["max"]) == (10.0, 30.0), setp
assert menu_cfg["dp_menu_2_1_1_2_2"][0] == "number"
wait(lambda: seen.get(f"{base}/dp_menu_2_1_1_1_1/state") == "34.5", "menu value state")
wait(lambda: seen.get(f"{base}/dp_menu_1_4_1_2/state") == "011511100047", "ProdNr state")
wait(lambda: seen.get(f"{base}/dp_menu_2_1_1_1_2/state") == "Sommer", "Jahreszeit state")

assert not fake_hpm.VIOLATIONS, fake_hpm.VIOLATIONS
assert num["name"] == "Brauchwasser Sollwert Nacht", num["name"]  # descriptive name from the editor page
assert configs["homeassistant/sensor/test_hpm/dp_33_6_1_1/config"]["name"] == "Anlagenübersicht Aussentemperatur"
assert configs["homeassistant/sensor/test_hpm/dp_7_6_2_6/config"]["name"] == "Anlagenübersicht Störmeldung HK1"
mode = configs["homeassistant/select/test_hpm/dp_ms_ms0/config"]
assert mode["options"] == fake_hpm.MODES, mode
assert not any("19_6_2_4" in t for t in configs), "unavailable value must be skipped"
assert not any("2_2_1_9" in t for t in configs), "status-page links without a value must be skipped"
assert configs["homeassistant/sensor/test_hpm/dp_34_6_1_2/config"]["name"] == "Anlagenübersicht Kurz"  # no Langtext
assert seen[f"{base}/dp_33_6_1_1/state"] == "-3.5"
assert seen[f"{base}/dp_126_6_6_22/state"] == "0"
assert seen[f"{base}/dp_102_2_2_1_4/state"] == "Ein"
assert seen[f"{base}/dp_60_1_1_1/state"] == "Auto"
assert seen[f"{base}/dp_ms_ms0/state"] == "Auto"

watcher.publish(f"{base}/dp_55_6_4_3/set", "52.5")
watcher.publish(f"{base}/dp_60_1_1_1/set", "Heizen")
watcher.publish(f"{base}/dp_37_6_4_7/set", "99")  # read-only value: must be ignored
watcher.publish(f"{base}/dp_ms_ms0/set", "Sommer")
watcher.publish(f"{base}/dp_menu_2_1_1_2_1/set", "21.5")  # Heizkreis 1 RaumNZ1 via the menu editor
wait(lambda: seen.get(f"{base}/dp_55_6_4_3/state") == "52.5", "number write")
wait(lambda: seen.get(f"{base}/dp_60_1_1_1/state") == "Heizen", "select write")
wait(lambda: seen.get(f"{base}/dp_ms_ms0/state") == "Sommer", "operating mode write")
wait(lambda: seen.get(f"{base}/dp_menu_2_1_1_2_1/state") == "21.5", "menu setting write", 30)
time.sleep(2)
assert fake_hpm.WRITES == [("55:6.4.3", "52.5"), ("60:1.1.1", "Heizen"), ("ms:MS0", "Sommer"), ("RaumNZ1", "21.5")], fake_hpm.WRITES

# Session expiry: drop all sessions, the bridge must log in again on the next poll.
fake_hpm.SESSIONS.clear()
fake_hpm.VALUES["37:6.4.7"][1] = "50.5"
fake_hpm.MENU[2][1][1][1][1][1][1][1][0][1] = "36.0 °C"
wait(lambda: seen.get(f"{base}/dp_37_6_4_7/state") == "50.5", "re-login after session expiry", 30)
wait(lambda: seen.get(f"{base}/dp_menu_2_1_1_1_1/state") == "36.0", "menu value poll after re-login", 30)
assert not fake_hpm.VIOLATIONS, fake_hpm.VIOLATIONS
assert any(p.name.startswith("editor_") for p in (tmp / "dump").iterdir())
assert (tmp / "dump" / "page_menu_1_3_1.html").exists()
assert not any("98765432" in p.read_text() for p in (tmp / "dump").iterdir()), "code pages must not be dumped"
assert any(sid in fake_hpm.UNLOCKED for sid in fake_hpm.SESSIONS) or fake_hpm.UNLOCKED, "access code was not entered"
# Logout frees the session; a restart re-uses a stored session instead of opening a new one.
bridge.client.logout()
assert not (tmp / "session").exists()

from hpm2mqtt.hpm import HpmClient, HpmLocked, parse_page  # noqa: E402
locked = HpmClient("127.0.0.1:18099", request_delay=0)
locked.get("menue.rsp?branchnr=1&level=1")
locked.get("menue.rsp?branchnr=3&level=2")
try:
    locked.get("menue.rsp?branchnr=2&level=3")
    raise AssertionError("code-protected menu must not open without access_code")
except HpmLocked:
    pass
wrong = HpmClient("127.0.0.1:18099", request_delay=0, access_code="1234")
wrong.get("menue.rsp?branchnr=1&level=1")
wrong.get("menue.rsp?branchnr=3&level=2")
try:
    wrong.get("menue.rsp?branchnr=2&level=3")
    raise AssertionError("wrong access code must be reported")
except HpmLocked:
    pass
js = parse_page('<a onclick="location.href=\'v21.rsp?sessionid=AB12\'">WP</a><frame src="menue.rsp?sessionid=AB12&amp;branchnr=2">'
                '<a href="leave.rsp?sessionid=AB12">x</a>', "v0.rsp")
assert {k for k, _ in js.links} == {"v21.rsp"}, js.links
# Status page and debug zip
import io, urllib.request, zipfile  # noqa: E401,E402
from hpm2mqtt import web  # noqa: E402
buf = web.LogBuffer()
buf.lines.append("hello log")
web.start(bridge, buf, 18100)
page = urllib.request.urlopen("http://127.0.0.1:18100/api/hassio_ingress/abc/").read().decode()
assert "SW-Spei" in page and "aquarea_hpm_debug.zip" in page and "Scan finished" in page, page[:800]
data = urllib.request.urlopen("http://127.0.0.1:18100/api/hassio_ingress/abc/aquarea_hpm_debug.zip").read()
names = zipfile.ZipFile(io.BytesIO(data)).namelist()
assert "log.txt" in names and "values.tsv" in names and any(n.startswith("pages/editor_") for n in names), names
print("ALL CHECKS PASSED")
