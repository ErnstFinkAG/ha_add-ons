# CTA CS19i Bridge (Home Assistant add-on) — v0.2.0

New configuration:
- `hostname` (controller host/IP), `controller_port`, `controller_password`
- `discovery_prefix` (MQTT discovery, default `homeassistant`)
- `mqtt_port`, `mqtt_username`, `mqtt_password` (server fixed to `localhost`)
- `state_base_topic` for state/command topics
- `log_pages`, `log_changes_only`

The add-on publishes MQTT Discovery to `<discovery_prefix>/sensor/.../config`
and state under `<state_base_topic>/<pageId>/<itemId>`.
It also exposes a button via discovery and listens for commands at
`<state_base_topic>/command/start_heating`.
