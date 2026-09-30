# Public Sensors

A small Home Assistant app/add-on that publishes selected Home Assistant history as a standalone, read-only web graph.

The browser does not receive a Home Assistant access token and cannot call Home Assistant services through this app. The app reads Home Assistant history through the internal Supervisor/Core API, caches the result, and exposes only a small display page and JSON data endpoint.

The first release supports:

- one numeric sensor, such as a water level;
- one optional ON/OFF entity, such as a pump switch;
- one threshold line;
- a fixed history period and fixed axes;
- hover tooltips only, with no zoom, pan, export, configuration, or control functions.

See DOCS.md for installation, configuration, and reverse-proxy examples.
