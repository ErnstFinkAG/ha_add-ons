#!/usr/bin/env python3
import argparse
import json
import re
import socket
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt  # type: ignore
except Exception:  # pragma: no cover
    mqtt = None  # type: ignore


ESC_SEQ_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ESC_SEQ_RE.sub("", s or "")


def read_frame(ip: str, port: int, timeout: int) -> str:
    deadline = time.time() + timeout
    buf = b""
    text = ""
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.settimeout(1.0)
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            text = buf.decode("ascii", errors="ignore")
            if "R_MODE MODE" in text and "C_SET TANK_SET" in text:
                # we have at least one full block
                break
    return text


def parse_block(text: str) -> Dict[str, Optional[float]]:
    cleaned = strip_ansi(text).replace("\r", "")
    data: Dict[str, Optional[float]] = {}

    # --- R_MODE temperatures / mode ---
    m_r = re.search(
        r"R_MODE MODE WATER_TMP_AD.*?\n([^\n]+)",
        cleaned,
        re.DOTALL,
    )
    if m_r:
        parts = m_r.group(1).split()
        if len(parts) >= 10:
            r_mode = parts[0]
            mode_map = {
                "00": "Off",
                "01": "Standby",
                "02": "DHW",
                "03": "Heating",
                "04": "Cooling",
            }
            data["mode"] = None  # publish as string separately
            data["mode_str"] = mode_map.get(r_mode.upper(), f"code_{r_mode}")

            def to_float(x: str) -> Optional[float]:
                try:
                    return float(int(x, 16 if re.fullmatch(r"[0-9A-Fa-f]+", x) else 10))
                except Exception:
                    return None

            # These are approximate, using the already-decoded integer columns
            data["water_temp"] = to_float(parts[3])
            data["indoor_temp"] = to_float(parts[5])
            data["tank_temp"] = to_float(parts[9])
            # best guess for outdoor from middle column if present
            data["outdoor_temp"] = to_float(parts[7]) if len(parts) > 7 else None

    # --- Setpoints ---
    m_s = re.search(
        r"C_SET TANK_SET .*?\n([^\n]+)",
        cleaned,
        re.DOTALL,
    )
    if m_s:
        parts = m_s.group(1).split()
        if len(parts) >= 2:
            try:
                c_set = int(parts[0], 16)
            except Exception:
                c_set = None
            try:
                t_set = int(parts[1], 16)
            except Exception:
                t_set = None
            if c_set is not None:
                data["flow_setpoint"] = float(c_set)
            if t_set is not None:
                data["tank_setpoint"] = float(t_set)

    # --- Compressor / pump ---
    m_c = re.search(
        r"CMP_REQ W_PUMP OUTPUT INPUT PM_TAP PM_DUTY PM_RPM.*?\n([^\n]+)",
        cleaned,
        re.DOTALL,
    )
    if m_c:
        parts = m_c.group(1).split()
        if len(parts) >= 7:
            def to_int(x: str) -> Optional[float]:
                try:
                    return float(int(x, 16 if re.fullmatch(r"[0-9A-Fa-f]+", x) else 10))
                except Exception:
                    return None

            data["compressor_req"] = to_int(parts[0])
            data["water_pump"] = to_int(parts[1])
            data["output_power_raw"] = to_int(parts[2])
            data["input_power_raw"] = to_int(parts[3])
            data["pump_duty"] = to_int(parts[5])
            data["pump_rpm"] = to_int(parts[6])

    # --- Outdoor unit power / frequency ---
    m_e = re.search(
        r"ER_CODE O_STATUS O_PIPE O_CUR O_DISC O_VAL COMPHZ HPOWER CPOWER TPOWER.*?\n([^\n]+)",
        cleaned,
        re.DOTALL,
    )
    if m_e:
        parts = m_e.group(1).split()
        # Example line:
        # 00 00     02      01    00     2A    78     00    0014   0000   0000
        if len(parts) >= 9:
            def to_int(x: str) -> Optional[float]:
                try:
                    return float(int(x, 16 if re.fullmatch(r"[0-9A-Fa-f]+", x) else 10))
                except Exception:
                    return None

            data["compressor_hz"] = to_int(parts[6])   # COMPHZ
            data["heat_power"] = to_int(parts[8])      # HPOWER approximation
    return data


@dataclass
class MqttCfg:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    discovery_prefix: str
    state_base: str


def mqtt_connect(cfg: MqttCfg):
    if mqtt is None:
        return None
    client = mqtt.Client()
    if cfg.username or cfg.password:
        client.username_pw_set(cfg.username or "", cfg.password or "")
    try:
        client.connect(cfg.host, cfg.port, keepalive=30)
        client.loop_start()
        return client
    except Exception as e:
        print(f"[ERROR] MQTT connect to {cfg.host}:{cfg.port} failed: {e}", file=sys.stderr)
        return None


def mqtt_publish(client, topic: str, payload: str, retain: bool = False):
    if client is None:
        return
    try:
        client.publish(topic, payload, qos=0, retain=retain)
    except Exception as e:
        print(f"[ERROR] MQTT publish to {topic} failed: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Panasonic F-series heatpump decoder for HA add-on.")
    ap.add_argument("--hp-ip", required=True)
    ap.add_argument("--hp-port", type=int, required=True)
    ap.add_argument("--device-name", default="panasonic_f")
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-username", default="")
    ap.add_argument("--mqtt-password", default="")
    ap.add_argument("--discovery-prefix", default="homeassistant")
    ap.add_argument("--state-base-topic", default="panasonic_f")

    args = ap.parse_args()

    print(f"[INFO ] Connecting to heatpump {args.hp_ip}:{args.hp_port} (timeout {args.timeout}s)")
    try:
        raw = read_frame(args.hp_ip, args.hp_port, args.timeout)
    except Exception as e:
        print(f"[ERROR] Failed to read from heatpump: {e}", file=sys.stderr)
        return 2

    print("[DIAG ] Raw frame snippet:\n" + strip_ansi(raw)[-400:])
    data = parse_block(raw)
    if not data:
        print("[ERROR] No recognizable Panasonic F block in stream", file=sys.stderr)
        return 3

    print("[INFO ] Decoded values:")
    for k in sorted(data.keys()):
        if k == "mode":
            continue
        print(f"  {k}: {data[k]}")

    mqtt_cfg = MqttCfg(
        host=args.mqtt_host or "localhost",
        port=args.mqtt_port,
        username=args.mqtt_username or None,
        password=args.mqtt_password or None,
        discovery_prefix=(args.discovery_prefix or "homeassistant").strip().strip("/"),
        state_base=(args.state_base_topic or "panasonic_f").strip().strip("/"),
    )

    client = mqtt_connect(mqtt_cfg)
    dev_slug = re.sub(r"[^a-z0-9_]+", "_", args.device_name.strip().lower())

    # basic device description
    ha_device = {
        "identifiers": [f"panasonic_f_{args.hp_ip}_{args.hp_port}"],
        "name": args.device_name,
        "manufacturer": "Panasonic",
        "model": "Aquarea F-series",
    }

    def publish_sensor(name: str, friendly: str, unit: Optional[str], device_class: Optional[str], state_class: Optional[str]):
        if name not in data or data[name] is None:
            return
        object_id = f"{dev_slug}_{name}"
        state_topic = f"{mqtt_cfg.state_base}/{dev_slug}/{name}/state"
        config_topic = f"{mqtt_cfg.discovery_prefix}/sensor/{object_id}/config"
        cfg = {
            "name": friendly,
            "unique_id": f"{dev_slug}_{name}",
            "object_id": object_id,
            "state_topic": state_topic,
            "unit_of_measurement": unit,
            "device": ha_device,
        }
        if device_class:
            cfg["device_class"] = device_class
        if state_class:
            cfg["state_class"] = state_class
        mqtt_publish(client, config_topic, json.dumps({k: v for k, v in cfg.items() if v is not None}), retain=True)
        mqtt_publish(client, state_topic, str(data[name]), retain=False)
        print(f"[DIAG ] MQTT -> {state_topic} = {data[name]}")

    # publish key metrics
    publish_sensor("water_temp", "Water temperature", "°C", "temperature", "measurement")
    publish_sensor("indoor_temp", "Indoor temperature", "°C", "temperature", "measurement")
    publish_sensor("tank_temp", "Tank temperature", "°C", "temperature", "measurement")
    publish_sensor("outdoor_temp", "Outdoor temperature", "°C", "temperature", "measurement")
    publish_sensor("flow_setpoint", "Flow setpoint", "°C", "temperature", "measurement")
    publish_sensor("tank_setpoint", "Tank setpoint", "°C", "temperature", "measurement")
    publish_sensor("compressor_hz", "Compressor frequency", "Hz", None, "measurement")
    publish_sensor("heat_power", "Heating power (raw)", "W", "power", "measurement")
    publish_sensor("pump_duty", "Pump duty", "%", None, "measurement")
    publish_sensor("pump_rpm", "Pump RPM", "rpm", None, "measurement")

    if client is not None:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
