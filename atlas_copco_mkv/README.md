# Atlas Copco MKV (Home Assistant Add-on)

Poll Atlas Copco MK5s Touch controllers and print a parsed table to the add-on logs.
This package contains a minimal working setup; it reads the first entry of each CSV
(`ip_list`, `name_list`, `type`, `timeout_list`) and runs a single poll.

> Tip: We can extend this to loop over all entries and publish via MQTT if desired.

## Options
- `ip_list`: CSV of controller IPs
- `name_list`: CSV of device names
- `interval_list`: Poll intervals (future use)
- `timeout_list`: Request timeouts (seconds)
- `verbose_list`: Verbose logging (future use)
- `type`: Controller types (GA15VP13, GA15VS23A)
- `mqtt_*`: MQTT connection details (reserved for future use)
- `discovery_prefix`: MQTT discovery prefix (reserved)

## Install
1. Copy the `atlas_copco_mkv` folder into your Home Assistant `/addons` directory.
2. In HA UI, go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories →** add local folder.
3. Find **Atlas Copco MKV** under *Local add-ons*, install, configure, and start.

## Roadmap
- Iterate over all CSV entries
- Publish to MQTT discovery and state topics
- Health metrics and retries
