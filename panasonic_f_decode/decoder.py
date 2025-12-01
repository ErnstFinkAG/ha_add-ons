import os
import re
import time
import socket
import paho.mqtt.client as mqtt

HP_IP = os.getenv("HP_IP")
HP_PORT = int(os.getenv("HP_PORT"))
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_PREFIX = os.getenv("MQTT_PREFIX")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1"))

SCALE_WATER = int(os.getenv("SCALE_WATER"))
SCALE_INDOOR = int(os.getenv("SCALE_INDOOR"))
SCALE_TANK = int(os.getenv("SCALE_TANK"))
SCALE_R1 = int(os.getenv("SCALE_R1"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

def log(msg):
    levels = {"debug": "[DEBUG]", "info": "[INFO]"}
    print(f"{levels.get(LOG_LEVEL,'[INFO]')} {msg}")

def ad_to_temp(value, scale):
    try:
        return round(int(value, 16) / scale, 2)
    except:
        return None

mqttc = mqtt.Client()
mqttc.username_pw_set(MQTT_USER, MQTT_PASSWORD)
mqttc.connect(MQTT_HOST, MQTT_PORT)
mqttc.loop_start()

def publish(sensor, value):
    mqttc.publish(f"{MQTT_PREFIX}/{sensor}", value, retain=True)

def autodiscover(sensor, name, unit):
    topic = f"homeassistant/sensor/{MQTT_PREFIX}_{sensor}/config"
    payload = (
        f'{{"name":"{name}","state_topic":"{MQTT_PREFIX}/{sensor}",'
        f'"unit_of_measurement":"{unit}","unique_id":"{MQTT_PREFIX}_{sensor}"}}'
    )
    mqttc.publish(topic, payload, retain=True)

sensors = [
    ("water_temp", "Water Temperature", "°C"),
    ("indoor_temp", "Indoor Temperature", "°C"),
    ("tank_temp", "Tank Temperature", "°C"),
    ("r1_temp", "Refrigerant Temperature", "°C"),
    ("flow_setpoint", "Flow Setpoint", "°C"),
    ("tank_setpoint", "Tank Setpoint", "°C"),
    ("mode", "Heatpump Mode", "")
]

for s,n,u in sensors:
    autodiscover(s,n,u)

BUFFER = ""

PATTERN_RMODE = re.compile(r"R_MODE.+?\n\s*([0-9A-Fa-f ]+)")
PATTERN_CSET  = re.compile(r"C_SET.+?\n\s*([0-9A-Fa-f ]+)")

log(f"Connecting to heat pump at {HP_IP}:{HP_PORT}")

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HP_IP, HP_PORT))
sock.setblocking(False)

log("Decoder running.")

while True:
    try:
        data = sock.recv(4096).decode(errors="ignore")
        BUFFER += data

        m = PATTERN_RMODE.search(BUFFER)
        if m:
            vals = m.group(1).split()
            publish("mode", vals[1])
            publish("water_temp", ad_to_temp(vals[2], SCALE_WATER))
            publish("indoor_temp", ad_to_temp(vals[4], SCALE_INDOOR))
            publish("r1_temp", ad_to_temp(vals[5], SCALE_R1))
            publish("tank_temp", ad_to_temp(vals[6], SCALE_TANK))
            BUFFER = BUFFER.replace(m.group(0), "")

        m2 = PATTERN_CSET.search(BUFFER)
        if m2:
            vals = m2.group(1).split()
            publish("flow_setpoint", int(vals[0],16))
            publish("tank_setpoint", int(vals[1],16))
            BUFFER = BUFFER.replace(m2.group(0), "")

    except BlockingIOError:
        pass
    except Exception as e:
        log(f"Error: {e}")

    time.sleep(POLL_INTERVAL)
