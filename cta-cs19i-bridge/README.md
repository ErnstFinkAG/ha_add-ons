# CTA CS19i Bridge (Home Assistant add-on)

Bridges a CTA CS19i heatpump controller over WebSocket (`Lux_WS`) to MQTT so Home Assistant auto-discovers sensors, plus a **Start Heating** button.

## Install
1. Copy this folder to `/addons/cta-cs19i-bridge/` on your Home Assistant host.
2. In **Settings → Add-ons → Add-on Store**, click the three dots (⋮) → **Reload**.
3. Find **CTA CS19i Bridge**, install, set options (host/password), and start.
4. Ensure the MQTT integration is configured (e.g., with the Mosquitto add-on).
5. Entities will appear under device **CTA CS19i**.

## Options
- `host` (default `10.80.21.11`), `port` (`8214`), `password` (`999990`)
- `poll_interval`: seconds between full polls
- `demand_delta_c`: °C bump for the **Start Heating** action
- `mqtt_prefix`: topic prefix; sensors publish under `${prefix}/state/...`

## Entities
- Dozens of sensors created by MQTT Discovery.
- Button: **CTA Start Heating** (under device CTA CS19i).

## Notes
- The add-on auto reconnects to the controller and MQTT broker.
- The **Start Heating** routine raises *Min. Rückl.Solltemp.* to create demand.
