import socket
import time
import paho.mqtt.client as mqtt
import json
import sys
import logging
from datetime import datetime
from typing import Tuple

CONFIG_PATH = "/data/options.json"
WH65LP_PAYLOAD_LEN = 21
WH65LP_START_BYTE = 0x24

def setup_logging(level: str):
    logger = logging.getLogger("wh65lp_bridge")
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )
    handler.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(handler)
    logger.propagate = False
    return logger

def get_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def hex_preview(data: bytes, max_len: int):
    if len(data) <= max_len:
        return data.hex(" ")
    return data[:max_len].hex(" ") + f" … (+{len(data)-max_len} bytes)"

def extract_frames(buffer: bytearray):
    while True:
        if len(buffer) < WH65LP_PAYLOAD_LEN:
            return
        try:
            start = buffer.index(WH65LP_START_BYTE)
        except ValueError:
            buffer.clear()
            return
        if len(buffer) < start + WH65LP_PAYLOAD_LEN:
            return
        frame = bytes(buffer[start:start + WH65LP_PAYLOAD_LEN])
        del buffer[:start + WH65LP_PAYLOAD_LEN]
        yield frame

def main():
    config = get_config()

    logger = setup_logging(config.get("log_level", "INFO").upper())
    log_raw_tcp = config.get("log_raw_tcp", False)
    log_mqtt = config.get("log_mqtt", True)
    log_max_bytes = config.get("log_max_bytes", 128)

    mqtt_client = mqtt.Client()
    if config.get("mqtt_user"):
        mqtt_client.username_pw_set(config["mqtt_user"], config.get("mqtt_pass"))

    def on_connect(client, userdata, flags, rc):
        logger.info("MQTT connected rc=%s", rc)

    mqtt_client.on_connect = on_connect
    mqtt_client.connect(config["mqtt_host"], config["mqtt_port"], 60)
    mqtt_client.loop_start()

    buffer = bytearray()

    while True:
        try:
            logger.info("Connecting to %s:%s", config["ws_host"], config["ws_port"])
            with socket.create_connection((config["ws_host"], config["ws_port"]), timeout=15) as s:
                logger.info("TCP connected")
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed")
                    buffer.extend(chunk)
                    if log_raw_tcp:
                        logger.debug("TCP RECV %s", hex_preview(chunk, log_max_bytes))
                    for frame in extract_frames(buffer):
                        logger.info("FRAME %s", frame.hex(" "))
                        topic = f"{config['mqtt_prefix']}/raw_frame"
                        payload = frame.hex()
                        mqtt_client.publish(topic, payload, retain=False)
                        if log_mqtt:
                            logger.info("MQTT PUB topic=%s payload=%s", topic, payload)
        except Exception as e:
            logger.warning("Socket error: %s", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
