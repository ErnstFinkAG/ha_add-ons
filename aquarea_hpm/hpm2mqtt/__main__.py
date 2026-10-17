"""Entry point: ``python3 -m hpm2mqtt [options.json]``."""

import json
import logging
import os
import signal
import sys

import requests

from . import web
from .bridge import Bridge


def mqtt_from_supervisor() -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {}
    try:
        resp = requests.get("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", {})
    except (requests.RequestException, ValueError) as err:
        logging.getLogger(__name__).warning("MQTT service not available from Supervisor: %s", err)
        return {}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/options.json"
    with open(path, encoding="utf-8") as handle:
        options = json.load(handle)
    logging.basicConfig(level=getattr(logging, str(options.get("log_level", "info")).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log_buffer = web.LogBuffer()
    logging.getLogger().addHandler(log_buffer)
    if not options.get("host"):
        sys.exit("Set 'host' (IP address of the HPM) in the add-on configuration")
    if options.get("mqtt_host"):
        # Own broker: take everything from the options, nothing from the Mosquitto add-on.
        mqtt_conf = {"host": options["mqtt_host"], "port": options.get("mqtt_port") or 1883,
                     "username": options.get("mqtt_username", ""), "password": options.get("mqtt_password", "")}
    else:
        mqtt_conf = mqtt_from_supervisor()
        if options.get("mqtt_username"):
            mqtt_conf["username"] = options["mqtt_username"]
            mqtt_conf["password"] = options.get("mqtt_password", "")
    if options.get("mqtt_host"):
        logging.info("Using the MQTT broker from the add-on options")
    elif mqtt_conf.get("host"):
        logging.info("Using the MQTT broker of the Mosquitto add-on")
    if not mqtt_conf.get("host"):
        sys.exit("No MQTT broker found: install the Mosquitto broker add-on or set mqtt_host")
    bridge = Bridge(options, mqtt_conf)

    def stop(signum, frame):
        # Free the HPM session slot so a restart or a browser can log in right away.
        bridge.client.logout()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    web.start(bridge, log_buffer, int(os.environ.get("INGRESS_PORT", 8099)))
    bridge.run()


if __name__ == "__main__":
    main()
