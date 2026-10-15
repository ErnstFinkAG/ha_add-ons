# CTA CS19i Bridge

Reads all pages of the CTA CS19i (Luxtronik) web interface over its WebSocket
(port 8214) and publishes every value to Home Assistant via MQTT discovery.

- One sensor per row, with stable IDs from page path + row name
- Temperatures, pressure, power, energy (kWh) and operating hours get proper
  device and state classes, so energy and hours work in the Energy dashboard
  and long-term statistics
- Abschaltungen / Fehlerspeicher are collapsed into one "Latest" sensor each,
  with the last 50 entries as attributes
- Discovery is retained and re-sent when Home Assistant restarts
- Reconnects to the controller with backoff; entities go unavailable when the add-on stops

## Thermostat (climate entity)

The bridge creates a `climate.cta_cs19i_heizung` entity, usable with the standard
thermostat card:

- **Mode**: the heating operating mode. `auto` = Automatik, `heat` = Party, `off` = Aus;
  the other modes (e.g. Zus. Wärmeerz, Ferien) are presets.
- **Target temperature**: the heating curve shift ("Temperatur +-"). The CS19i heats
  weather-compensated, so this is an offset (e.g. +2 °C = warmer), not a room setpoint.
- **Current temperature**: room temperature if the controller has one, else Rücklauf.

The settings are found automatically; the add-on log shows which ones with `[climate] using ...`.
To pick others, set `climate_mode_setting`, `climate_temperature_setting` or
`climate_current_temperature` to `Page path/Row name`, e.g. `Einstellungen/Betriebsart/Heizkreis`.
Changes need a `controller_password` that is allowed to change these settings.

## Options

- `mqtt_username` / `mqtt_password`: leave empty to use the Mosquitto add-on login from the Supervisor
- `include_path_prefixes`: comma-separated page paths to publish (empty = all), e.g. `Informationen/Temperaturen,Informationen/Betriebsstunden`
- `skip_path_prefixes`: comma-separated page paths to leave out (default `Zugang:`)
- `log_pages` / `log_changes_only`: print page values to the add-on log, only changed values by default
- `climate`: create the thermostat entity (default on)
- `writable_path_prefixes`: comma-separated settings pages whose values become HA selects/numbers
  you can change (empty = none). The log lists pages that have changeable values.
- `cleanup_discovery`: after the first complete cycle, remove retained discovery configs of this bridge
  that no longer match a current value (e.g. per-entry log entities from old versions)
