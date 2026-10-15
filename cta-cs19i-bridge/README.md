# CTA CS19i Bridge

Reads all pages of the CTA CS19i (Luxtronik) web interface over its WebSocket
(port 8214) and publishes every value to Home Assistant via MQTT discovery.

- Stable MQTT IDs (page path + item name)
- Numeric-only MQTT state payloads when unit is set
- Abschaltungen / Fehlerspeicher collapsed into one "Latest" sensor each
- Reconnects to the controller with backoff; marks entities unavailable when the add-on stops
- Leave `mqtt_username` empty to use the Mosquitto add-on credentials from the Supervisor

See `config.yaml` for options.
