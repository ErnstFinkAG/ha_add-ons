import socket
import time
import paho.mqtt.client as mqtt
import json
import sys
from typing import Dict, Tuple, Optional

CONFIG_PATH = "/data/options.json"

WH65LP_PAYLOAD_LEN = 21
WH65LP_START_BYTE = 0x24

def get_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def crc8_poly31_msb(data: bytes, init: int = 0x00) -> int:
    """CRC-8 with poly 0x31, MSB-first, init 0x00."""
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
    """Decode a 21-byte WH65LP payload that starts with 0x24."""
    if len(p) != WH65LP_PAYLOAD_LEN:
        raise ValueError(f"Expected {WH65LP_PAYLOAD_LEN} bytes, got {len(p)}")
    if p[0] != WH65LP_START_BYTE:
        raise ValueError(f"Payload does not start with 0x24: got 0x{p[0]:02X}")

    # Validate CRC + checksums (per WH65LP transmission protocol)
    crc_expected = p[15]
    crc_calc = crc8_poly31_msb(p[:15])
    if crc_calc != crc_expected:
        raise ValueError(f"CRC mismatch: calc=0x{crc_calc:02X} expected=0x{crc_expected:02X}")

    checksum_expected = p[16]
    checksum_calc = sum(p[:16]) & 0xFF  # includes CRC byte
    if checksum_calc != checksum_expected:
        raise ValueError(f"Checksum mismatch: calc=0x{checksum_calc:02X} expected=0x{checksum_expected:02X}")

    pressure_checksum_expected = p[20]
    pressure_checksum_calc = (p[17] + p[18] + p[19]) & 0xFF
    if pressure_checksum_calc != pressure_checksum_expected:
        raise ValueError(
            f"Pressure checksum mismatch: calc=0x{pressure_checksum_calc:02X} expected=0x{pressure_checksum_expected:02X}"
        )

    temperature: Dict[str, Optional[float]] = {}
    wind: Dict[str, Optional[float]] = {}
    sun: Dict[str, Optional[float]] = {}
    rain: Dict[str, Optional[float]] = {}
    debug: Dict[str, Optional[object]] = {}

    debug["family_code"] = p[0]
    debug["security_code"] = p[1]

    # Wind direction is already in degrees in the payload (0-359 encoded into a byte in practice)
    wind_dir_deg = p[2]
    wind["wind_direction_deg"] = int(wind_dir_deg)

    # Temperature: 11-bit value across bytes 3-4; bit3 of byte3 is low-battery flag
    low_battery = bool((p[3] >> 3) & 0x01)
    temp_raw = ((p[3] & 0x07) << 8) | p[4]   # 0..2047
    temperature_c = round((temp_raw - 400) / 10.0, 1) if temp_raw != 0x7FF else None
    temperature["temperature_C"] = temperature_c
    temperature["humidity_percent"] = (p[5] if p[5] != 0xFF else None)

    # Wind speed: byte6, gust: byte7
    wsp_raw = p[6]
    wind["windspeed_mps"] = (round(wsp_raw * 0.51 / 8.0, 2) if wsp_raw != 0xFF else None)
    gust_raw = p[7]
    wind["gust_speed_mps"] = (round(gust_raw * 0.51, 2) if gust_raw != 0xFF else None)

    # Rain
    rain_raw = (p[8] << 8) | p[9]
    rain["rainfall_mm"] = (round(rain_raw * 0.254, 2) if rain_raw != 0xFFFF else None)

    # UV + Light
    uv_raw = (p[10] << 8) | p[11]
    sun["uv_uW_cm2"] = (uv_raw if uv_raw != 0xFFFF else None)

    light_raw = (p[12] << 16) | (p[13] << 8) | p[14]
    sun["light_lux"] = (round(light_raw / 10.0, 1) if light_raw != 0xFFFFFF else None)

    # Pressure (24-bit, /100 -> hPa)
    pressure_raw = (p[17] << 16) | (p[18] << 8) | p[19]
    sun["pressure_hpa"] = (round(pressure_raw / 100.0, 2) if pressure_raw != 0xFFFFFF else None)

    debug["low_battery"] = int(low_battery)
    debug["temp_raw"] = temp_raw
    debug["wind_speed_raw"] = wsp_raw
    debug["gust_raw"] = gust_raw
    debug["rain_raw"] = rain_raw
    debug["uv_raw"] = uv_raw
    debug["light_raw"] = light_raw
    debug["pressure_raw"] = pressure_raw
    debug["crc"] = crc_expected
    debug["checksum"] = checksum_expected
    debug["pressure_checksum"] = pressure_checksum_expected

    return temperature, wind, sun, rain, debug

def extract_wh65lp_payloads(buffer: bytearray):
    """Yield WH65LP 21-byte payloads from a TCP byte stream buffer."""
    while True:
        try:
            start = buffer.index(WH65LP_START_BYTE)
        except ValueError:
            # No start byte in buffer; avoid unbounded growth
            if len(buffer) > 4096:
                del buffer[:-64]
            return

        # Drop leading noise / wrappers before 0x24
        if start > 0:
            del buffer[:start]

        if len(buffer) < WH65LP_PAYLOAD_LEN:
            return

        payload = bytes(buffer[:WH65LP_PAYLOAD_LEN])

        # If it's not valid (CRC), advance one byte and resync
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
        print("[FATAL] unique_prefix option must be set in add-on options and not be empty.")
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
            print(f"[DISCOVERY] Published discovery for {name} ({discovery_topic})")

    def on_connect(client, userdata, flags, rc):
        print("[INFO] MQTT connected, sending discovery...")
        send_discovery()

    mqtt_client.on_connect = on_connect

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

    mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()
    time.sleep(2)

    print(f"[INFO] Connecting to {WS_HOST}:{WS_PORT} ...")

    backoff = 1.0
    buffer = bytearray()

    while True:
        try:
            with socket.create_connection((WS_HOST, WS_PORT), timeout=15) as s:
                s.settimeout(15)
                print("[INFO] Connected. Listening for WH65LP payloads (0x24 ... 21 bytes).\n")
                backoff = 1.0
                buffer.clear()

                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed by peer")
                    buffer.extend(chunk)

                    for payload in extract_wh65lp_payloads(buffer):
                        temp, wind, sun, rain, debug = decode_wh65lp_payload(payload)
                        publish_all(temp, wind, sun, rain, debug)

                        # Optional debug line (comment out if noisy)
                        print(
                            f"[OK] T={temp.get('temperature_C')}°C H={temp.get('humidity_percent')}% "
                            f"WD={wind.get('wind_direction_deg')}° WS={wind.get('windspeed_mps')}m/s "
                            f"G={wind.get('gust_speed_mps')}m/s P={sun.get('pressure_hpa')}hPa"
                        )
        except KeyboardInterrupt:
            print("[INFO] Stopping.")
            break
        except Exception as e:
            print(f"[WARN] Socket error: {e}. Reconnecting in {backoff:.1f}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 60.0)

if __name__ == "__main__":
    main()
