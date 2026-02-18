import socket
import time
import paho.mqtt.client as mqtt
import json
import sys
import logging
from typing import Dict, Tuple, Optional

CONFIG_PATH = "/data/options.json"

WH65LP_PAYLOAD_LEN = 21
WH65LP_START_BYTE = 0x24


# ----------------------------
# Logging Configuration
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def crc8_poly31_msb(data: bytes, init: int = 0x00) -> int:
    crc = init & 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) & 0xFF) ^ 0x31
            else:
                crc = (crc << 1) & 0xFF
    return crc & 0xFF


def decode_wh65lp_payload(p: bytes) -> Tuple[Dict, Dict, Dict, Dict, Dict]:
    if len(p) != WH65LP_PAYLOAD_LEN:
        raise ValueError(f"Expected {WH65LP_PAYLOAD_LEN} bytes, got {len(p)}")
    if p[0] != WH65LP_START_BYTE:
        raise ValueError(f"Payload does not start with 0x24: got 0x{p[0]:02X}")

    crc_expected = p[15]
    crc_calc = crc8_poly31_msb(p[:15])
    if crc_calc != crc_expected:
        raise ValueError(f"CRC mismatch: calc=0x{crc_calc:02X} expected=0x{crc_expected:02X}")

    checksum_expected = p[16]
    checksum_calc = sum(p[:16]) & 0xFF
    if checksum_calc != checksum_expected:
        raise ValueError(f"Checksum mismatch: calc=0x{checksum_calc:02X} expected=0x{checksum_expected:02X}")

    pressure_checksum_expected = p[20]
    pressure_checksum_calc = (p[17] + p[18] + p[19]) & 0xFF
    if pressure_checksum_calc != pressure_checksum_expected:
        raise ValueError(
            f"Pressure checksum mismatch: calc=0x{pressure_checksum_calc:02X} "
            f"expected=0x{pressure_checksum_expected:02X}"
        )

    temperature: Dict[str, Optional[float]] = {}
    wind: Dict[str, Optional[float]] = {}
    sun: Dict[str, Optional[float]] = {}
    rain: Dict[str, Optional[float]] = {}
    debug: Dict[str, Optional[object]] = {}

    debug["family_code"] = p[0]
    debug["security_code"] = p[1]

    wind["wind_direction_deg"] = int(p[2])

    low_battery = bool((p[3] >> 3) & 0x01)
    temp_raw = ((p[3] & 0x07) << 8) | p[4]
    temperature["temperature_C"] = round((temp_raw - 400) / 10.0, 1) if temp_raw != 0x7FF else None
    temperature["humidity_percent"] = p[5] if p[5] != 0xFF else None

    wsp_raw = p[6]
    wind["windspeed_mps"] = round(wsp_raw * 0.51 / 8.0, 2) if wsp_raw != 0xFF else None
    gust_raw = p[7]
    wind["gust_speed_mps"] = round(gust_raw * 0.51, 2) if gust_raw != 0xFF else None

    rain_raw = (p[8] << 8) | p[9]
    rain["rainfall_mm"] = round(rain_raw * 0.254, 2) if rain_raw != 0xFFFF else None

    uv_raw = (p[10] << 8) | p[11]
    sun["uv_uW_cm2"] = uv_raw if uv_raw != 0xFFFF else None

    light_raw = (p[12] << 16) | (p[13] << 8) | p[14]
    sun["light_lux"] = round(light_raw / 10.0, 1) if light_raw != 0xFFFFFF else None

    pressure_raw = (p[17] << 16) | (p[18] << 8) | p[19]
    sun["pressure_hpa"] = round(pressure_raw / 100.0, 2) if pressure_raw != 0xFFFFFF else None

    debug["low_battery"] = int(low_battery)

    return temperature, wind, sun, rain, debug


def extract_wh65lp_payloads(buffer: bytearray):
    while True:
        try:
            start = buffer.index(WH65LP_START_BYTE)
        except ValueError:
            if len(buffer) > 4096:
                del buffer[:-64]
            return

        if start > 0:
            del buffer[:start]

        if len(buffer) < WH65LP_PAYLOAD_LEN:
            return

        payload = bytes(buffer[:WH65LP_PAYLOAD_LEN])

        try:
            _ = decode_wh65lp_payload(payload)
        except Exception:
            del buffer[0:1]
            continue

        yield payload
        del buffer[:WH65LP_PAYLOAD_LEN]


def main():
    config = get_config()

    MQTT_HOST = config["mqtt_host"]
    MQTT_PORT = config["mqtt_port"]
    MQTT_USER = config.get("mqtt_user", "")
    MQTT_PASS = config.get("mqtt_pass", "")
    MQTT_PREFIX = config["mqtt_prefix"].rstrip("/")
    DISCOVERY_PREFIX = config["discovery_prefix"].rstrip("/")
    WS_HOST = config["ws_host"]
    WS_PORT = int(config["ws_port"])
    UNIQUE_PREFIX = (config.get("unique_prefix") or "").strip()

    if not UNIQUE_PREFIX:
        logging.fatal("unique_prefix option must be set and not be empty.")
        sys.exit(1)

    mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)

    if MQTT_USER:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

    def mqtt_publish(sensor_id: str, value, retain: bool = True):
        topic = f"{MQTT_PREFIX}/{sensor_id}"
        mqtt_client.publish(topic, value, retain=retain)

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
                    "model": "WH65LP",
                },
            }

            if unit:
                payload["unit_of_measurement"] = unit
            if sensor_id == "low_battery":
                payload["device_class"] = "battery"

            discovery_topic = f"{DISCOVERY_PREFIX}/sensor/{unique_id}/config"
            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            logging.info(f"Published discovery for {name}")

    def on_connect(client, userdata, flags, rc):
        logging.info("MQTT connected, sending discovery...")
        send_discovery()

    mqtt_client.on_connect = on_connect

    mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()
    time.sleep(2)

    logging.info(f"Connecting to {WS_HOST}:{WS_PORT} ...")

    backoff = 1.0
    buffer = bytearray()

    while True:
        try:
            with socket.create_connection((WS_HOST, WS_PORT), timeout=15) as s:
                s.settimeout(30)
                logging.info("Connected. Listening for WH65LP payloads.")
                backoff = 1.0
                buffer.clear()

                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed by peer")

                    buffer.extend(chunk)

                    for payload in extract_wh65lp_payloads(buffer):
                        temp, wind, sun, rain, debug = decode_wh65lp_payload(payload)

                        mqtt_publish("temperature_C", temp.get("temperature_C"))
                        mqtt_publish("humidity_percent", temp.get("humidity_percent"))
                        mqtt_publish("wind_direction_deg", wind.get("wind_direction_deg"))
                        mqtt_publish("windspeed_mps", wind.get("windspeed_mps"))
                        mqtt_publish("gust_speed_mps", wind.get("gust_speed_mps"))
                        mqtt_publish("rainfall_mm", rain.get("rainfall_mm"))
                        mqtt_publish("uv_uW_cm2", sun.get("uv_uW_cm2"))
                        mqtt_publish("light_lux", sun.get("light_lux"))
                        mqtt_publish("pressure_hpa", sun.get("pressure_hpa"))
                        mqtt_publish("low_battery", debug.get("low_battery"))

                        logging.info(
                            f"T={temp.get('temperature_C')}°C "
                            f"H={temp.get('humidity_percent')}% "
                            f"WS={wind.get('windspeed_mps')}m/s "
                            f"P={sun.get('pressure_hpa')}hPa"
                        )

        except KeyboardInterrupt:
            logging.info("Stopping.")
            break
        except Exception as e:
            logging.warning(f"Socket error: {e}. Reconnecting in {backoff:.1f}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 60.0)


if __name__ == "__main__":
    main()
