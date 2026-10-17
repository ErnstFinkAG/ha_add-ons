# Aquarea HPM Bridge

Reads the web interface of a Panasonic Aquarea Heat Pump Manager (PAW-HPM / HPM1, shown as
`HPM-800F22` on the network) and publishes every value it finds to Home Assistant through
MQTT discovery. Values the HPM lets you change get a `number` or `select` entity; everything
else becomes a `sensor`.

## Requirements

- The Mosquitto broker add-on (or any broker set via `mqtt_host`) and the MQTT integration.
- The HPM reachable from Home Assistant over the LAN (default `10.80.30.71`).

## How it works

1. Opens `http://<host>/`, which redirects to `v0.rsp?sessionid=…`, and keeps that session.
   If the web login is enabled, set `password` (and `username` only if the login asks for
   one). Deeper menus that ask for another code get `access_code`.
2. Follows every `v*.rsp` page and `menue.rsp` menu link (never logout or layout links) and
   collects each value link (`vinfo.rsp?…&id=37:6.4.7`). The HPM id becomes the entity's
   unique id, so names can change without losing history.
3. For values inside a `setgrvalue.rsp` form it opens the editor page (like clicking the value
   in a browser, nothing is changed) to learn whether it is a list (select) or a number and
   its min/max.
4. Overview pages are polled every `scan_interval`, menu/parameter pages every
   `parameter_interval`, and a full rescan runs every `rescan_hours` or when you press the
   **Rescan values** button.

Setting a value repeats what the browser does: open the editor, fill in the value, submit with
the OK button. Numbers outside the min/max the HPM shows are refused.

## Troubleshooting

Turn on `dump_html` and restart the add-on, then open the add-on's web UI and press
**Download debug zip**: it contains the log, the list of values and every page and editor the
add-on read (also kept in `/share/aquarea_hpm/`). If a value is missing or a changeable value shows only
as a sensor, those files show what the HPM sent.

The HPM allows only a few parallel web sessions. Avoid very short poll intervals and close the
HPM web page in your browser if the add-on reports connection errors.
