import os
import re
import time
import socket
import paho.mqtt.client as mqtt

HP_IP = os.getenv("HP_IP")
HP_PORT = int(os.getenv("HP_PORT", "2000") or "2000")
MQTT_HOST = os.getenv("MQTT_HOST") or "homeassistant.local"
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883") or "1883")
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
MQTT_PREFIX = os.getenv("MQTT_PREFIX", "panasonic_f")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1") or "1")

SCALE_WATER = int(os.getenv("SCALE_WATER", "100") or "100")
SCALE_INDOOR = int(os.getenv("SCALE_INDOOR", "100") or "100")
SCALE_TANK = int(os.getenv("SCALE_TANK", "100") or "100")
SCALE_R1 = int(os.getenv("SCALE_R1", "100") or "100")

LOG_LEVEL = (os.getenv("LOG_LEVEL") or "info").lower()

def log(level, msg):
    if LOG_LEVEL != "debug" and level == "debug":
        return
    print(f"[{level.upper()}] {msg}", flush=True)

def ad_to_temp(value, scale):
    try:
        return round(int(value, 16) / scale, 2)
    except Exception:
        return None

def make_mqtt_client():
    client = mqtt.Client()
    if MQTT_USER is not None:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()
    return client

mqttc = make_mqtt_client()

def publish(sensor, value):
    topic = f"{MQTT_PREFIX}/{sensor}"
    mqttc.publish(topic, value, retain=True)

def autodiscover(sensor, name, unit=""):
    topic = f"homeassistant/sensor/{MQTT_PREFIX}_{sensor}/config"
    payload = {
        "name": name,
        "state_topic": f"{MQTT_PREFIX}/{sensor}",
        "unique_id": f"{MQTT_PREFIX}_{sensor}",
    }
    if unit:
        payload["unit_of_measurement"] = unit
    import json
    mqttc.publish(topic, json.dumps(payload), retain=True)

SENSORS = [
    ("mode", "Heatpump Mode", ""),
    ("water_temp", "Water Temperature", "°C"),
    ("water2_temp", "Water Temperature 2", "°C"),
    ("indoor_temp", "Indoor Temperature", "°C"),
    ("r1_temp", "Refrigerant Temperature", "°C"),
    ("tank_temp", "Tank Temperature", "°C"),
    ("outdoor_temp", "Outdoor Temperature", "°C"),
    ("flow_setpoint", "Flow Setpoint", "°C"),
    ("tank_setpoint", "Tank Setpoint", "°C"),
    ("room_setpoint", "Room Setpoint", "°C"),
    ("cmp_req", "Compressor Request", ""),
    ("pump_rpm", "Pump RPM", "rpm"),
    ("pump_duty", "Pump Duty", "%"),
    ("pump_output", "Pump Output", ""),
    ("pump_input", "Pump Input", ""),
    ("error_code", "Error Code", ""),
    ("compressor_hz", "Compressor Frequency", "Hz"),
    ("heating_power", "Heating Power", "W"),
    ("cooling_power", "Cooling Power", "W"),
    ("tank_power", "Tank Power", "W"),
]

DEBUG_SENSORS = [
    ("raw_rmode", "Raw R_MODE Line", ""),
    ("raw_cmp", "Raw CMP_REQ Line", ""),
    ("raw_diff", "Raw DIFF Line", ""),
    ("raw_cset", "Raw C_SET Line", ""),
    ("raw_er", "Raw ER_CODE Line", "")
]

for s, n, u in SENSORS + DEBUG_SENSORS:
    autodiscover(s, n, u)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RMODE_RE = re.compile(r"R_MODE MODE WATER_TMP_AD.*?\n\s*([0-9A-Fa-f ]+)")
CMP_RE = re.compile(r"CMP_REQ W_PUMP OUTPUT INPUT PM_TAP PM_DUTY PM_RPM.*?\n\s*([0-9A-Fa-f ]+)")
DIFF_RE = re.compile(r"DIFF REMO_SET IN_SET SHIFT:OUT_AIR STARTUP.*?\n\s*([0-9A-Fa-f ]+)")
CSET_RE = re.compile(r"C_SET TANK_SET F_SEL H_TIME T_TIME TDELAY STERTMP STERTM.*?\n\s*([0-9A-Fa-f ]+)")
ER_RE = re.compile(r"ER_CODE O_STATUS O_PIPE O_CUR O_DISC O_VAL COMPHZ HPOWER CPOWER TPOWER.*?\n\s*([0-9A-Fa-f ]+)")

BUFFER = ""

log("info", f"Connecting to heat pump at {HP_IP}:{HP_PORT}")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HP_IP, HP_PORT))
sock.setblocking(False)
log("info", "TCP connection established, starting decode loop.")

while True:
    try:
        try:
            data = sock.recv(4096)
            if data:
                chunk = data.decode(errors="ignore")
                chunk = ANSI_RE.sub("", chunk)
                BUFFER += chunk
        except BlockingIOError:
            pass

        m = RMODE_RE.search(BUFFER)
        if m:
            line = m.group(1).strip()
            vals = line.split()
            publish("raw_rmode", line)
            if len(vals) >= 7:
                mode_raw = vals[1]
                publish("mode", mode_raw)
                publish("water_temp", ad_to_temp(vals[2], SCALE_WATER))
                publish("water2_temp", ad_to_temp(vals[3], SCALE_WATER))
                publish("indoor_temp", ad_to_temp(vals[4], SCALE_INDOOR))
                publish("r1_temp", ad_to_temp(vals[5], SCALE_R1))
                publish("tank_temp", ad_to_temp(vals[6], SCALE_TANK))
                if len(vals) > 8:
                    publish("outdoor_temp", ad_to_temp(vals[8], SCALE_INDOOR))
            BUFFER = BUFFER.replace(m.group(0), "")

        m = CMP_RE.search(BUFFER)
        if m:
            line = m.group(1).strip()
            vals = line.split()
            publish("raw_cmp", line)
            if len(vals) >= 7:
                publish("cmp_req", vals[0])
                publish("pump_output", vals[2])
                publish("pump_input", vals[3])
                publish("pump_duty", vals[5])
                publish("pump_rpm", vals[6])
            BUFFER = BUFFER.replace(m.group(0), "")

        m = DIFF_RE.search(BUFFER)
        if m:
            line = m.group(1).strip()
            vals = line.split()
            publish("raw_diff", line)
            if len(vals) >= 3:
                try:
                    room_set = int(vals[2], 16) / 100.0
                    publish("room_setpoint", round(room_set, 2))
                except Exception:
                    pass
            BUFFER = BUFFER.replace(m.group(0), "")

        m = CSET_RE.search(BUFFER)
        if m:
            line = m.group(1).strip()
            vals = line.split()
            publish("raw_cset", line)
            if len(vals) >= 2:
                try:
                    publish("flow_setpoint", int(vals[0], 16))
                    publish("tank_setpoint", int(vals[1], 16))
                except Exception:
                    pass
            BUFFER = BUFFER.replace(m.group(0), "")

        m = ER_RE.search(BUFFER)
        if m:
            line = m.group(1).strip()
            vals = line.split()
            publish("raw_er", line)
            if len(vals) >= 10:
                publish("error_code", vals[0])
                try:
                    publish("compressor_hz", int(vals[6], 16))
                    publish("heating_power", int(vals[7], 16))
                    publish("cooling_power", int(vals[8], 16))
                    publish("tank_power", int(vals[9], 16))
                except Exception:
                    pass
            BUFFER = BUFFER.replace(m.group(0), "")

    except Exception as e:
        log("info", f"Error in loop: {e}")

    time.sleep(POLL_INTERVAL)
