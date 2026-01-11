# Atlas Copco MKV (Home Assistant Add-on)

Poll Atlas Copco MK5s Touch controllers and print a parsed table to the add-on logs.
This package contains a minimal working setup; it reads the first entry of each CSV
(`ip_list`, `name_list`, `type`, `timeout_list`) and runs a single poll.

## Options
- `ip_list`: CSV of controller IPs
- `name_list`: CSV of device names
- `interval_list`: Poll intervals (future use)
- `timeout_list`: Request timeouts (seconds)
- `verbose_list`: Verbose logging (future use)
- `type`: Controller types (GA15VP13, GA15VS23A)
- `mqtt_*`: MQTT connection details
- `discovery_prefix`: MQTT discovery prefix