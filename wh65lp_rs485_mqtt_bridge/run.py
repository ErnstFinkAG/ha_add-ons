import socket
import time
import paho.mqtt.client as mqtt
import json
import os
import sys

CONFIG_PATH = "/data/options.json"

def get_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def main():
    # --- Load config from options.json ---
    config = get_config()
    MQTT_HOST = config["mqtt_host"]
    MQTT_PORT = config["mqtt_port"]
    MQTT_USER = config["mqtt_user"]
    MQTT_PASS = config["mqtt_pass"]
    MQTT_PREFIX = config["mqtt_prefix"]
    DISCOVERY_PREFIX = config["discovery_prefix"]
    WS_HOST = config["ws_host"]
    WS_PORT = config["ws_port"]
    PACKET_SIZE = config["packet_size"]
    UNIQUE_PREFIX = config.get("unique_prefix", None)

    if not UNIQUE_PREFIX or not UNIQUE_PREFIX.strip():
        print("[FATAL] unique_prefix option must be set in add-on options and not be empty.")
        sys.exit(1)

    # --- MQTT Setup ---
    mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)
    if MQTT_USER:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

    def mqtt_publish(topic, value, retain=True):
        full_topic = f"{MQTT_PREFIX}/{topic}"
        mqtt_client.publish(full_topic, value, retain=retain)
        print(f"[MQTT] {full_topic} = {value}")

    # --- Home Assistant MQTT Discovery ---
    def send_discovery():
        sensors = [
            ("temperature_C", "Temperatur", "°C"),
            ("humidity_percent", "Feuchte", "%"),
            ("wind_direction_deg", "Windrichtung", "°"),
            ("windspeed_mps", "Wind", "m/s"),
            ("gust_speed_mps", "Böe", "m/s"),
            ("uv_uW_cm2", "UV", "uW/cm²"),
            ("light_lux", "Licht", "lx"),
            ("pressure_hpa", "Luftdruck", "hPa"),
            ("rainfall_mm", "Regen", "mm"),
            ("low_battery", "Batterie schwach", None),
        ]
        for sensor_id, name, unit in sensors:
            unique_id = f"{UNIQUE_PREFIX}_{sensor_id}"
            state_topic = f"{MQTT_PREFIX}/{sensor_id}"
            payload = {
                "name": f"{UNIQUE_PREFIX.upper()} {name}",
                "state_topic": state_topic,
                "unique_id": unique_id,
                "device": {
                    "identifiers": [f"{UNIQUE_PREFIX}_rs485"],
                    "name": f"{UNIQUE_PREFIX.upper()} Wetterstation",
                    "manufacturer": "Misol",
                    "model": "WH65LP"
                }
            }
            if unit:
                payload["unit_of_measurement"] = unit
            if sensor_id == "low_battery":
                payload["device_class"] = "battery"
            topic = f"{DISCOVERY_PREFIX}/sensor/{unique_id}/config"
            # --- DEBUG LOGGING ---
            print(f"[DISCOVERY-DEBUG] Sensor: {sensor_id}")
            print(f"  unique_id: {unique_id}")
            print(f"  discovery_topic: {topic}")
            print(f"  discovery_payload: {json.dumps(payload)}")
            # --- SEND DISCOVERY ---
            mqtt_client.publish(topic, json.dumps(payload), retain=True)
            print(f"[DISCOVERY] Published discovery for {name} ({topic})")

    def on_connect(client, userdata, flags, rc):
        print("[INFO] MQTT connected, sending discovery...")
        send_discovery()

    mqtt_client.on_connect = on_connect

    # --- Packet Decoder ---
    def decode_packet(data):
        if len(data) != PACKET_SIZE:
            raise ValueError("Invalid packet size")

        temperature = {}
        wind = {}
        sun = {}
        rain = {}
        debug = {}

        # -- WIND DIRECTION FIX: Scale 0-255 -> 0-359°
        wind_dir_raw = data[2]
        wind_dir_deg = int(wind_dir_raw * 360 / 256)  # scale 0-255 to 0-359°
        wind["wind_direction_deg"] = wind_dir_deg

        dir_h = (data[3] >> 4) & 0x0F
        tmp_h = data[3] & 0x0F
        debug["low_battery"] = bool((tmp_h >> 3) & 0x01)
        tmp_10 = (tmp_h >> 2) & 0x01
        tmp_9 = (tmp_h >> 1) & 0x01
        tmp_8 = tmp_h & 0x01

        tmp_m = (data[4] >> 4) & 0x0F
        tmp_l = data[4] & 0x0F
        tmp_7 = (tmp_m >> 3) & 0x01
        tmp_6 = (tmp_m >> 2) & 0x01
        tmp_5 = (tmp_m >> 1) & 0x01
        tmp_3 = (tmp_l >> 3) & 0x01
        tmp_2 = (tmp_l >> 2) & 0x01
        tmp_1 = (tmp_l >> 1) & 0x01
        tmp_0 = tmp_l & 0x01
        tmp_raw = (
            (tmp_10 << 10) |
            (tmp_9 << 9) |
            (tmp_8 << 8) |
            (tmp_7 << 7) |
            (tmp_6 << 6) |
            (tmp_5 << 5) |
            (tmp_3 << 3) |
            (tmp_2 << 2) |
            (tmp_1 << 1) |
            (tmp_0 << 0)
        )
        temperature["temperature_C"] = round((tmp_raw - 400) / 10.0, 1)
        debug["TMP_raw"] = tmp_raw

        hum = data[5]
        temperature["humidity_percent"] = hum if hum != 0xFF else None

        wsp_high = (data[6] >> 4) & 0x0F
        wsp_low = data[6] & 0x0F
        wsp_raw = (wsp_high << 4) | wsp_low
        wind["windspeed_mps"] = round(wsp_raw * 0.51 / 8, 2) if wsp_raw != 0x7FF else None
        debug["WSP_raw"] = wsp_raw

        gust = data[7]
        wind["gust_speed_mps"] = round(gust * 0.51, 2) if gust != 0xFF else None

        rain_raw = (data[8] << 8) | data[9]
        rain["rainfall_mm"] = round(rain_raw * 0.254, 2)
        debug["rain_raw"] = rain_raw

        uv_raw = (data[10] << 8) | data[11]
        sun["uv_uW_cm2"] = uv_raw

        light_raw = (data[12] << 16) | (data[13] << 8) | data[14]
        sun["light_lux"] = round(light_raw / 10) if light_raw != 0xFFFFFF else None
        debug["light_raw"] = light_raw

        pressure_raw = ((data[17] & 0x7F) << 16) | (data[18] << 8) | data[19]
        sun["pressure_hpa"] = round(pressure_raw / 100.0, 2) if pressure_raw != 0x1FFFF else None
        debug["pressure_raw"] = pressure_raw

        debug["low_battery"] = int(bool((tmp_h >> 3) & 0x01))

        return temperature, wind, sun, rain, debug

    def publish_all(temperature, wind, sun, rain, debug):
        mqtt_publish("temperature_C", temperature.get("temperature_C"))
        mqtt_publish("humidity_percent", temperature.get("humidity_percent"))
        mqtt_publish("wind_direction_deg", wind.get("wind_direction_deg"))
        mqtt_publish("windspeed_mps", wind.get("windspeed_mps"))
        mqtt_publish("gust_speed_mps", wind.get("gust_speed_mps"))
        mqtt_publish("rainfall_mm", rain.get("rainfall_mm"))
        mqtt_publish("uv_uW_cm2", sun.get("uv_uW_cm2"))
        mqtt_publish("light_lux", sun.get("light_lux"))
        mqtt_publish("pressure_hpa", sun.get("pressure_hpa"))
        mqtt_publish("low_battery", debug.get("low_battery"))
        print("[DEBUG] Published MQTT for all categories.")
        print("------------------------------------------------------------")

    # --- Main Loop ---
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()
    time.sleep(2)  # Give MQTT time to connect and send discovery

    print(f"[INFO] Connecting to {WS_HOST}:{WS_PORT}...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((WS_HOST, WS_PORT))
            print("[INFO] Connected. Listening for packets...\n")

            while True:
                packet = s.recv(PACKET_SIZE)
                if not packet:
                    print("[!] Connection closed.")
                    break

                if len(packet) < PACKET_SIZE:
                    print(f"[!] Incomplete packet ({len(packet)} bytes). Skipping...")
                    continue

                try:
                    temp, wind, sun, rain, debug = decode_packet(packet)
                    publish_all(temp, wind, sun, rain, debug)
                except Exception as e:
                    print(f"[!] Failed to decode or publish packet: {e}")

                time.sleep(1)

    except Exception as e:
        print(f"[FATAL] {e}")

if __name__ == "__main__":
    main()
