import json
import logging
import os
import signal
import socket
import sys
import time
import urllib.request
from typing import Dict, Optional, Tuple

import paho.mqtt.client as mqtt

CONFIG_PATH = "/data/options.json"

WH65LP_PAYLOAD_LEN = 21
WH65LP_CORE_LEN = 17  # sensor data incl. CRC and sum, without the pressure block
WH65LP_START_BYTE = 0x24
STUFFED_BYTE = 0xFA  # some gateways send 0xFA as 0xFA 0x00

# The station sends a frame every ~16 s. Mark the device unavailable after
# STALE_AFTER seconds without a valid frame and reconnect after RECONNECT_AFTER.
STALE_AFTER = 60
RECONNECT_AFTER = 90

# UV raw value (uW/cm²) upper bounds for UV index 0..12, >= last bound is 13.
# Table from the rtl_433 Fine Offset WH24/WH65 decoder.
UVI_UPPER = [432, 851, 1210, 1570, 2017, 2450, 2761, 3100, 3512, 3918, 4277, 4650, 5029]

# (sensor_id, component, name, unit, device_class, state_class)
SENSORS = [
    ("temperature_C", "sensor", "Temperatur", "°C", "temperature", "measurement"),
    ("humidity_percent", "sensor", "Feuchte", "%", "humidity", "measurement"),
    ("wind_direction_deg", "sensor", "Windrichtung", "°", None, None),
    ("windspeed_mps", "sensor", "Wind", "m/s", "wind_speed", "measurement"),
    ("gust_speed_mps", "sensor", "Böe", "m/s", "wind_speed", "measurement"),
    ("uv_uW_cm2", "sensor", "UV", "uW/cm²", None, "measurement"),
    ("uv_index", "sensor", "UV-Index", None, None, "measurement"),
    ("light_lux", "sensor", "Licht", "lx", "illuminance", "measurement"),
    ("pressure_hpa", "sensor", "Luftdruck", "hPa", "atmospheric_pressure", "measurement"),
    ("rainfall_mm", "sensor", "Regen", "mm", "precipitation", "total_increasing"),
    ("low_battery", "binary_sensor", "Batterie schwach", None, "battery", None),
]

# Components a sensor was announced under in earlier versions and must be removed from.
LEGACY_COMPONENTS = {"low_battery": "sensor"}


def get_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_supervisor_mqtt() -> Optional[Dict]:
    """Return the MQTT service (Mosquitto add-on) credentials from the Supervisor, if any."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("data") or None
    except Exception as e:
        logging.info(f"No MQTT service from the Supervisor ({e}).")
        return None


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


def uv_index(uv_raw: int) -> int:
    index = 0
    while index < len(UVI_UPPER) and UVI_UPPER[index] < uv_raw:
        index += 1
    return index


class FrameError(ValueError):
    """A candidate frame failed validation; reason is a short key for the statistics."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def check_core(p: bytes):
    """Validate the 17-byte sensor part (family byte, CRC-8, sum)."""
    if len(p) < WH65LP_CORE_LEN:
        raise FrameError("short", f"Expected at least {WH65LP_CORE_LEN} bytes, got {len(p)}")
    if p[0] != WH65LP_START_BYTE:
        raise FrameError("start", f"Payload does not start with 0x24: got 0x{p[0]:02X}")

    crc_expected = p[15]
    crc_calc = crc8_poly31_msb(p[:15])
    if crc_calc != crc_expected:
        raise FrameError("crc", f"CRC mismatch: calc=0x{crc_calc:02X} expected=0x{crc_expected:02X}")

    checksum_expected = p[16]
    checksum_calc = sum(p[:16]) & 0xFF
    if checksum_calc != checksum_expected:
        raise FrameError("checksum", f"Checksum mismatch: calc=0x{checksum_calc:02X} expected=0x{checksum_expected:02X}")


def pressure_valid(p: bytes) -> bool:
    return len(p) >= WH65LP_PAYLOAD_LEN and (p[17] + p[18] + p[19]) & 0xFF == p[20]


def decode_wh65lp_payload(p: bytes) -> Tuple[Dict, Dict, Dict, Dict, Dict]:
    """Decode a frame. pressure_hpa is left out when its 4-byte block is missing or invalid."""
    check_core(p)
    has_pressure = pressure_valid(p)

    temperature: Dict[str, Optional[float]] = {}
    wind: Dict[str, Optional[float]] = {}
    sun: Dict[str, Optional[float]] = {}
    rain: Dict[str, Optional[float]] = {}
    debug: Dict[str, Optional[object]] = {}

    debug["family_code"] = p[0]
    debug["security_code"] = p[1]

    # Wind direction is a 9-bit value:
    #   - low 8 bits in p[2]
    #   - bit8 is the MSB of p[3] (matches manual example "65E" => 357°)
    wind_dir_raw = p[2] | ((p[3] & 0x80) << 1)  # adds 256 if p[3] bit7 is set

    # Optional sanity: only publish plausible degree values (0..360)
    wind["wind_direction_deg"] = int(wind_dir_raw) if wind_dir_raw <= 360 else None

    low_battery = bool((p[3] >> 3) & 0x01)
    temp_raw = ((p[3] & 0x07) << 8) | p[4]
    temperature["temperature_C"] = round((temp_raw - 400) / 10.0, 1) if temp_raw != 0x7FF else None
    temperature["humidity_percent"] = p[5] if p[5] != 0xFF else None

    # Wind speed is also 9 bits: bit8 is bit4 of p[3] (0x1FF = invalid)
    wsp_raw = p[6] | ((p[3] & 0x10) << 4)
    wind["windspeed_mps"] = round(wsp_raw * 0.51 / 8.0, 2) if wsp_raw != 0x1FF else None
    gust_raw = p[7]
    wind["gust_speed_mps"] = round(gust_raw * 0.51, 2) if gust_raw != 0xFF else None

    rain_raw = (p[8] << 8) | p[9]
    rain["rainfall_mm"] = round(rain_raw * 0.254, 2) if rain_raw != 0xFFFF else None

    uv_raw = (p[10] << 8) | p[11]
    sun["uv_uW_cm2"] = uv_raw if uv_raw != 0xFFFF else None
    sun["uv_index"] = uv_index(uv_raw) if uv_raw != 0xFFFF else None

    light_raw = (p[12] << 16) | (p[13] << 8) | p[14]
    sun["light_lux"] = round(light_raw / 10.0, 1) if light_raw != 0xFFFFFF else None

    if has_pressure:
        pressure_raw = (p[17] << 16) | (p[18] << 8) | p[19]
        sun["pressure_hpa"] = round(pressure_raw / 100.0, 2) if pressure_raw != 0xFFFFFF else None

    debug["low_battery"] = int(low_battery)

    return temperature, wind, sun, rain, debug


def new_stats() -> Dict[str, int]:
    return {"bytes": 0, "frames": 0, "skipped": 0, "crc": 0, "checksum": 0, "no_pressure": 0, "unstuffed": 0}


def format_stats(stats: Dict[str, int]) -> str:
    return (
        f"{stats['bytes']} bytes received, {stats['frames']} valid frames, "
        f"{stats['crc']} CRC errors, {stats['checksum']} checksum errors, "
        f"{stats['no_pressure']} frames without valid pressure, "
        f"{stats['unstuffed']} frames repaired (0xFA 0x00), {stats['skipped']} bytes skipped"
    )


def unstuff(buffer: bytearray, length: int) -> Tuple[bytes, list]:
    """Read up to length bytes from buffer, dropping a 0x00 that follows 0xFA.

    Some RS485 gateways insert 0x00 after every 0xFA byte they forward. Returns
    the cleaned bytes and, for each of them, how many raw bytes it ends at.
    """
    out = bytearray()
    ends = []
    i = 0
    while i < len(buffer) and len(out) < length:
        out.append(buffer[i])
        i += 1
        if buffer[i - 1] == STUFFED_BYTE and i < len(buffer) and buffer[i] == 0x00:
            i += 1
        ends.append(i)
    return bytes(out), ends


def extract_wh65lp_payloads(buffer: bytearray, stats: Optional[Dict[str, int]] = None):
    """Yield decoded frames found in buffer, consuming them.

    A frame is 21 bytes: 17 bytes of sensor data checked by CRC-8 and sum, then
    4 bytes of pressure with their own checksum. When the pressure block is
    invalid, only the 17 checked bytes are consumed, so a short or corrupted
    pressure block does not swallow the start of the next frame.

    A frame that fails as received is tried again with the gateway's 0xFA 0x00
    stuffing removed (see unstuff).
    """
    if stats is None:
        stats = new_stats()
    while True:
        try:
            start = buffer.index(WH65LP_START_BYTE)
        except ValueError:
            stats["skipped"] += len(buffer)
            buffer.clear()
            return

        if start > 0:
            stats["skipped"] += start
            del buffer[:start]

        if len(buffer) < WH65LP_PAYLOAD_LEN:
            return

        payload = bytes(buffer[:WH65LP_PAYLOAD_LEN])
        ends = list(range(1, WH65LP_PAYLOAD_LEN + 1))

        try:
            decoded = decode_wh65lp_payload(payload)
        except FrameError as e:
            cleaned, cleaned_ends = unstuff(buffer, WH65LP_PAYLOAD_LEN)
            if cleaned == payload:
                decoded = None
            elif len(cleaned) < WH65LP_PAYLOAD_LEN:
                # Stuffing made the frame longer than what has arrived so far.
                return
            else:
                try:
                    decoded = decode_wh65lp_payload(cleaned)
                    payload, ends = cleaned, cleaned_ends
                    stats["unstuffed"] += 1
                except FrameError:
                    decoded = None
            if decoded is None:
                stats[e.reason] = stats.get(e.reason, 0) + 1
                logging.debug(f"Rejected frame {payload.hex(' ')}: {e}")
                stats["skipped"] += 1
                del buffer[0:1]
                continue

        stats["frames"] += 1
        if pressure_valid(payload):
            consumed = ends[WH65LP_PAYLOAD_LEN - 1]
        else:
            consumed = ends[WH65LP_CORE_LEN - 1]
            stats["no_pressure"] += 1
            logging.debug(f"Frame without valid pressure block: {payload.hex(' ')}")
        yield decoded
        del buffer[:consumed]


def stats_since(stats: Dict[str, int], earlier: Dict[str, int]) -> Dict[str, int]:
    return {k: stats[k] - earlier.get(k, 0) for k in stats}


def explain_gap(delta: Dict[str, int]) -> str:
    """One-line hint on where the frames got lost, from the counters of a gap."""
    if delta["bytes"] == 0:
        return (
            "Nothing arrived from the gateway: the station is not sending, or the "
            "gateway is not forwarding serial data on this TCP connection."
        )
    if delta["crc"] or delta["checksum"]:
        return (
            "Data arrives but fails the checksum: check baud rate (9600 8N1), "
            "RS485 wiring, termination and grounding."
        )
    return "Data arrives but contains no WH65LP frame (0x24): check the gateway mode and serial settings."


def enable_keepalive(sock: socket.socket):
    """Let the OS detect a dead gateway connection instead of waiting on a silent socket."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)


def build_discovery(discovery_prefix: str, unique_prefix: str, mqtt_prefix: str, availability_topic: str):
    """Return (topic, payload) pairs for HA MQTT discovery."""
    device = {
        "identifiers": [f"{unique_prefix}_rs485"],
        "name": f"{unique_prefix.upper()} Wetterstation",
        "manufacturer": "Misol",
        "model": "WH65LP",
    }
    messages = []
    for sensor_id, component, name, unit, device_class, state_class in SENSORS:
        unique_id = f"{unique_prefix}_{sensor_id}"
        payload = {
            "name": name,
            "state_topic": f"{mqtt_prefix}/{sensor_id}",
            "unique_id": unique_id,
            "availability_topic": availability_topic,
            "device": device,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class
        if component == "binary_sensor":
            payload["payload_on"] = "1"
            payload["payload_off"] = "0"

        legacy = LEGACY_COMPONENTS.get(sensor_id)
        if legacy and legacy != component:
            # Empty retained config removes the entity from its old component
            messages.append((f"{discovery_prefix}/{legacy}/{unique_id}/config", ""))
        messages.append((f"{discovery_prefix}/{component}/{unique_id}/config", json.dumps(payload)))
    return messages



def main():
    config = get_config()

    log_level = str(config.get("log_level") or "info").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    MQTT_HOST = (config.get("mqtt_host") or "").strip()
    MQTT_PORT = int(config.get("mqtt_port") or 1883)
    MQTT_USER = config.get("mqtt_user") or ""
    MQTT_PASS = config.get("mqtt_pass") or ""
    MQTT_PREFIX = config["mqtt_prefix"].rstrip("/")
    DISCOVERY_PREFIX = (config.get("discovery_prefix") or "homeassistant").rstrip("/")
    WS_HOST = config["ws_host"]
    WS_PORT = int(config["ws_port"])
    UNIQUE_PREFIX = (config.get("unique_prefix") or "").strip()

    if not UNIQUE_PREFIX:
        logging.fatal("unique_prefix option must be set and not be empty.")
        sys.exit(1)

    # Use the Mosquitto add-on when no broker is configured. "localhost" is
    # treated the same, since it only worked with host networking.
    if MQTT_HOST in ("", "localhost", "127.0.0.1"):
        service = get_supervisor_mqtt()
        if service:
            MQTT_HOST = service["host"]
            MQTT_PORT = int(service["port"])
            if not MQTT_USER:
                MQTT_USER = service.get("username") or ""
                MQTT_PASS = service.get("password") or ""
            logging.info(f"Using MQTT broker from Supervisor: {MQTT_HOST}:{MQTT_PORT}")
        elif not MQTT_HOST:
            logging.fatal("No mqtt_host configured and no MQTT service available.")
            sys.exit(1)

    availability_topic = f"{MQTT_PREFIX}/status"
    state = {"available": False}

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    if MQTT_USER:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.will_set(availability_topic, "offline", retain=True)
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)

    def mqtt_publish(sensor_id: str, value, retain: bool = True):
        # "None" makes HA show the value as unknown; an empty payload would be ignored.
        payload = "None" if value is None else value
        mqtt_client.publish(f"{MQTT_PREFIX}/{sensor_id}", payload, retain=retain)

    def set_available(available: bool, force: bool = False):
        if available != state["available"] or force:
            state["available"] = available
            mqtt_client.publish(availability_topic, "online" if available else "offline", retain=True)
            if not force:
                logging.info("Weather station %s.", "online" if available else "offline")

    def send_discovery():
        for topic, payload in build_discovery(DISCOVERY_PREFIX, UNIQUE_PREFIX, MQTT_PREFIX, availability_topic):
            mqtt_client.publish(topic, payload, retain=True)
            logging.debug(f"Published discovery {topic}")
        logging.info(f"Published discovery for {len(SENSORS)} entities.")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logging.error(f"MQTT connection refused: {reason_code}")
            return
        logging.info(f"MQTT connected to {MQTT_HOST}:{MQTT_PORT}, sending discovery...")
        send_discovery()
        set_available(state["available"], force=True)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            logging.warning(f"MQTT disconnected ({reason_code}), reconnecting...")

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect

    # connect_async + loop_start keeps retrying until the broker is reachable.
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()

    def handle_sigterm(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    backoff = 1.0
    buffer = bytearray()

    try:
        while True:
            try:
                logging.info(f"Connecting to {WS_HOST}:{WS_PORT} ...")
                with socket.create_connection((WS_HOST, WS_PORT), timeout=15) as s:
                    s.settimeout(5)
                    enable_keepalive(s)
                    logging.info("Connected. Listening for WH65LP payloads.")
                    backoff = 1.0
                    buffer.clear()
                    last_frame = time.monotonic()
                    stats = new_stats()
                    # Counters at the last valid frame, to report what arrived since then.
                    at_last_frame = dict(stats)
                    reported_gap = False

                    while True:
                        try:
                            chunk = s.recv(4096)
                            if not chunk:
                                raise ConnectionError("Connection closed by peer")
                            stats["bytes"] += len(chunk)
                            buffer.extend(chunk)
                        except socket.timeout:
                            pass

                        for temp, wind, sun, rain, debug in extract_wh65lp_payloads(buffer, stats):
                            last_frame = time.monotonic()
                            at_last_frame = dict(stats)
                            reported_gap = False

                            mqtt_publish("temperature_C", temp.get("temperature_C"))
                            mqtt_publish("humidity_percent", temp.get("humidity_percent"))
                            mqtt_publish("wind_direction_deg", wind.get("wind_direction_deg"))
                            mqtt_publish("windspeed_mps", wind.get("windspeed_mps"))
                            mqtt_publish("gust_speed_mps", wind.get("gust_speed_mps"))
                            mqtt_publish("rainfall_mm", rain.get("rainfall_mm"))
                            mqtt_publish("uv_uW_cm2", sun.get("uv_uW_cm2"))
                            mqtt_publish("uv_index", sun.get("uv_index"))
                            mqtt_publish("light_lux", sun.get("light_lux"))
                            if "pressure_hpa" in sun:
                                mqtt_publish("pressure_hpa", sun["pressure_hpa"])
                            mqtt_publish("low_battery", debug.get("low_battery"))
                            set_available(True)

                            logging.debug(
                                "T=%s°C H=%s%% WD=%s° WS=%sm/s G=%sm/s UV=%s uW/cm² (UVI %s) L=%s lx "
                                "P=%s hPa R=%s mm BatLow=%s",
                                temp.get("temperature_C"),
                                temp.get("humidity_percent"),
                                wind.get("wind_direction_deg"),
                                wind.get("windspeed_mps"),
                                wind.get("gust_speed_mps"),
                                sun.get("uv_uW_cm2"),
                                sun.get("uv_index"),
                                sun.get("light_lux"),
                                sun.get("pressure_hpa"),
                                rain.get("rainfall_mm"),
                                debug.get("low_battery"),
                            )

                        silent = time.monotonic() - last_frame
                        if silent > STALE_AFTER:
                            set_available(False)
                            if not reported_gap:
                                reported_gap = True
                                logging.warning(
                                    f"No valid frame for {int(silent)}s; since the last frame: "
                                    f"{format_stats(stats_since(stats, at_last_frame))}. "
                                    + explain_gap(stats_since(stats, at_last_frame))
                                )
                        if silent > RECONNECT_AFTER:
                            raise TimeoutError(
                                f"No valid frame for {int(silent)}s "
                                f"({format_stats(stats_since(stats, at_last_frame))})"
                            )

            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception as e:
                logging.warning(f"Socket error: {e}. Reconnecting in {backoff:.1f}s...")
                set_available(False)
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)
    except (SystemExit, KeyboardInterrupt):
        logging.info("Stopping.")
    finally:
        info = mqtt_client.publish(availability_topic, "offline", retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except Exception:
            pass
        mqtt_client.disconnect()
        mqtt_client.loop_stop()


if __name__ == "__main__":
    main()
