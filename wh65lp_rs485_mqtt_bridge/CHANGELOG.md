WH65LP RS485 to MQTT Bridge - Change Log
========================================

## 1.2.5 - 2026-10-15
- Fix wind speed above 16.3 m/s: read the 9th bit (byte 3, bit 4).
- MQTT: retry until the broker is reachable instead of exiting at startup.
- MQTT: use the Mosquitto add-on automatically when `mqtt_host` is empty or `localhost`; host networking removed.
- Availability topic with last will: entities show unavailable when no frame arrives for 60 s or the add-on stops.
- Invalid values are published as `None` (unknown) instead of an empty message.
- "Batterie schwach" is now a binary sensor (old battery sensor is removed automatically).
- Device and state classes on all sensors, so HA keeps long-term statistics; rain is `total_increasing`.
- New UV index sensor.
- Entity names no longer repeat the device name.
- New `log_level` option; per-frame values are logged at debug level.
- `mqtt_pass` is masked in the UI; neutral defaults; removed unused `build.json`.
- paho-mqtt callback API v2.

## 1.2.4 - 2026-02-27
- Winddirection sanitycheck added

## 1.1.6 - 2025-08-04
- debug: windirection now 360°, fixed 255 limit

## 1.1.5 - 2025-08-04
- First full-release


## 1.1.3 - 2025-08-04
- Added extra debug logging for MQTT discovery topics and unique IDs.
- Improved error handling if `unique_prefix` is missing or empty.
- Updated README with setup, usage, and troubleshooting sections.

## 1.1.2 - 2025-08-03
- Added support for fully configurable MQTT discovery prefix.
- Made MQTT and sensor topics fully customizable via options.
- Added example sensor YAML for manual Home Assistant configuration.

## 1.1.1 - 2025-07-31
- Now enforces that `unique_prefix` must be set in config.
- Added better MQTT connection diagnostics in logs.

## 1.1.0 - 2025-07-28
- Initial public release on GitHub.
- MQTT Home Assistant Discovery fully supported.
- Publishes all WH65LP weather station sensors to MQTT.
- Supports configuration of all connection and topic parameters.

## Earlier
- Private/internal versions for initial prototyping.

