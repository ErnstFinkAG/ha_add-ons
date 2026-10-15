# Atlas Copco MKV (Home Assistant Add-on)

Poll Atlas Copco MK5s Touch controllers and publish their values as Home Assistant
sensors via MQTT discovery. Every controller in `ip_list` gets its own polling loop.

## Options
All `*_list` options are comma-separated and matched by position (first IP goes with
the first name, type, interval, ...).

- `ip_list`: Controller IPs
- `name_list`: Device names (used for the HA device and entity IDs)
- `type`: Controller types (`GA15VP13` or `GA15VS23A`)
- `interval_list`: Poll interval per device in seconds (default 10)
- `timeout_list`: HTTP request timeout per device in seconds (default 5)
- `verbose_list`: `true` prints the full value table and every MQTT topic to the log on
  each poll; `false` prints one summary line per poll
- `mqtt_*`: MQTT connection details
- `discovery_prefix`: MQTT discovery prefix
- `state_base_topic`: Base topic for states and availability

## Notes
- Each device publishes `online`/`offline` to `<state_base_topic>/<device>/availability`.
  Sensors show as unavailable when the controller cannot be reached, and also when no
  update arrived for 3 poll intervals (minimum 60 s), e.g. because the add-on stopped.
- A failed poll is logged and retried on the next interval; it no longer stops polling
  for that device.
- Metrics with unit `count` (and status `code`) are published **without** `unit_of_measurement` so they show as plain numbers
  and do not break long-term statistics when Home Assistant previously stored those sensors as unitless.
