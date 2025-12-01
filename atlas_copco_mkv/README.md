# Panasonic F-Series Heatpump (Home Assistant Add-on)

This add-on connects to a Panasonic Aquarea F-series heatpump via a CH9121
TCP-to-serial adapter and publishes decoded values to MQTT with Home Assistant
discovery.

Based on your working Atlas Copco MKV add-on structure.

## Configuration

All fields are editable in the add-on UI:

- `hp_ip` : IP of the CH9121 (e.g. `10.80.255.221`)
- `hp_port` : TCP port (e.g. `2000`)
- `device_name` : Friendly name used for MQTT topics
- `poll_interval` : Seconds between polls
- `connect_timeout` : Socket timeout in seconds
- `mqtt_host` : MQTT broker host (e.g. `localhost`)
- `mqtt_port` : MQTT broker port (default 1883)
- `mqtt_username` / `mqtt_password` : optional credentials
- `discovery_prefix` : Home Assistant MQTT discovery prefix (default `homeassistant`)
- `state_base_topic` : Base topic for state messages (default `panasonic_f`)

The add-on runs in `host_network: true`, so `localhost` will point at the
Home Assistant host and can be used for the MQTT broker just like in your
other add-ons.
