# Public Sensors

Public Sensors publishes selected Home Assistant history as standalone, read-only web pages.

Each configured page is independent and has its own URL path. For example:

- `https://public.example.com/groundwater/`
- `https://public.example.com/tank-level/`
- `https://public.example.com/temperature/`

Each page can define its own entity, title, unit, axis, decimal places, history range, averaging interval, optional ON/OFF companion entity, and optional threshold.

The browser does not receive a Home Assistant access token and cannot call Home Assistant services through this app. The app reads Home Assistant through the internal Supervisor/Core API and exposes only read-only graph data.

See `DOCS.md` for configuration and reverse-proxy examples.
