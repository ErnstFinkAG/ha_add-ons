# WH65LP MQTT Bridge (Detailed Logging)

This Home Assistant add-on connects to a WH65LP weather station via TCP
and publishes decoded values to MQTT.

## New Features

- ISO-8601 timestamps with milliseconds
- Raw TCP receive logging (optional)
- Full frame hex dump
- Decoded payload debug logging
- Detailed MQTT publish logs (topic, payload, retain, mid)
- Configurable log level

Set `log_level` to `DEBUG` for maximum verbosity.
