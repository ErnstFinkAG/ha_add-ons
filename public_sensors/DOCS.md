# Public Sensors

## Purpose

Public Sensors publishes selected Home Assistant history on separate, read-only URL paths without exposing the Home Assistant frontend.

The app runs as its own Home Assistant OS container on TCP port 8098. It reads Home Assistant Core through the internal Supervisor proxy. The browser receives only the graph page and the selected cached history data.

Home Assistant credentials and SUPERVISOR_TOKEN stay inside the app container.

## URL model

Each configured page has its own path.

For example, with the public reverse-proxy hostname:

    public.fink-holzbau.ch

the default groundwater page is:

    https://public.fink-holzbau.ch/groundwater/

A second configured page with:

    path: outside-temperature

would be:

    https://public.fink-holzbau.ch/outside-temperature/

`show_index` controls the root path. When enabled, `/` shows a read-only list of all enabled Public Sensors pages. When disabled, `/` returns 404. Unknown page paths always return 404.

Each page has its own data endpoint under the same path:

    /groundwater/data
    /outside-temperature/data

## Default configuration

Version 0.3.0 starts with the index enabled and one page:

    browser_refresh_seconds: 60
    ha_refresh_seconds: 300
    timezone: Europe/Zurich
    show_index: true

    pages:
      - path: groundwater
        enabled: true
        title: Grundwasserstand und Pump Verlauf
        sensor_entity: sensor.efimmo_bw_b1_f0_r0_sen0_groundwaterlevel
        sensor_name: Wasserstand
        sensor_unit: mm
        decimals: 1
        sensor_min: 500
        sensor_max: 1000
        binary_entity: switch.pumpe_1
        binary_name: Pumpe
        history_hours: 48
        group_minutes: 5
        threshold_enabled: true
        threshold_value: 660
        threshold_label: Trigger 660 mm

The numeric series is averaged into the configured time buckets. The optional ON/OFF entity is shown as a step line and keeps its state changes.

## Add another public page

In Settings → Apps → Public Sensors → Configuration, add another entry under Public pages.

Example:

    path: outside-temperature
    enabled: true
    title: Aussentemperatur
    sensor_entity: sensor.outside_temperature
    sensor_name: Aussentemperatur
    sensor_unit: °C
    decimals: 1
    sensor_min: -20
    sensor_max: 45
    binary_entity: ""
    binary_name: Status
    history_hours: 48
    group_minutes: 5
    threshold_enabled: false
    threshold_value: 0
    threshold_label: ""

After saving the configuration, restart Public Sensors.

The new page is then:

    https://public.fink-holzbau.ch/outside-temperature/

The path must contain only lowercase letters, numbers, hyphens, and underscores.

## Configuration fields

### Global options

`browser_refresh_seconds`

How often an open browser requests cached graph data.

`ha_refresh_seconds`

How often the app refreshes Recorder history from Home Assistant.

`timezone`

IANA timezone used for graph labels, for example `Europe/Zurich`.

`show_index`

When `true`, the root URL `/` lists all enabled public sensor pages as links. When `false`, the root URL returns 404 and direct sensor URLs continue to work.

### Per-page options

`path`

Unique URL path for this page. Example: `groundwater`.

`enabled`

Controls whether this page is published.

`title`

Title shown above the graph.

`sensor_entity`

Numeric Home Assistant entity whose Recorder history is displayed.

`sensor_name`

Display name for the numeric series.

`sensor_unit`

Unit shown on the graph. Leave empty to use the entity's Home Assistant `unit_of_measurement`.

`decimals`

Number of decimal places shown in the current value and tooltip.

`sensor_min` and `sensor_max`

Fixed left-axis range.

`binary_entity`

Optional ON/OFF entity such as a switch. Leave empty if it is not required.

`binary_name`

Display name for the ON/OFF series.

`history_hours`

Recorder history period for this page.

`group_minutes`

Averaging bucket for the numeric history.

`threshold_enabled`

Controls whether the threshold line is shown.

`threshold_value`

Numeric position of the threshold line.

`threshold_label`

Text shown at the threshold line.

## Home Assistant configuration UI

Home Assistant app schemas support nested arrays, so each Public Sensors page appears as its own item in the app configuration. The fields of one page do not affect another page.

The standard app schema supports fixed lists, but it does not provide a dynamic Home Assistant entity selector populated from the live entity registry. For version 0.3.0, enter the entity ID in the page configuration.

A future Ingress-only administration page can add live entity dropdowns without exposing that administration UI through the public reverse proxy.

## Internal test

After updating and starting the app, check the log.

A successful refresh looks similar to:

    Page '/groundwater/' refreshed: 326 numeric points

From the internal network, the index is:

    http://HOME_ASSISTANT_IP:8098/

and the groundwater page is:

    http://HOME_ASSISTANT_IP:8098/groundwater/

Health check:

    http://HOME_ASSISTANT_IP:8098/healthz

Unknown paths return 404:

    http://HOME_ASSISTANT_IP:8098/not-configured/

## Recommended nginx layout

Keep Home Assistant and Public Sensors separate:

    ga.fink-holzbau.ch
        -> HOME_ASSISTANT_IP:8123

    public.fink-holzbau.ch
        -> HOME_ASSISTANT_IP:8098

Do not proxy `public.fink-holzbau.ch` to port 8123.

Because port 8098 contains only the read-only Public Sensors application, nginx can proxy the whole public hostname to that port. The application itself accepts only configured page paths.

Example:

    server {
        listen 80;
        server_name public.fink-holzbau.ch;

        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name public.fink-holzbau.ch;

        ssl_certificate /etc/letsencrypt/live/public.fink-holzbau.ch/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/public.fink-holzbau.ch/privkey.pem;

        ssl_protocols TLSv1.2 TLSv1.3;

        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "no-referrer" always;

        auth_basic "Public Sensors";
        auth_basic_user_file /etc/nginx/.htpasswd-public-sensors;

        location / {
            proxy_pass http://HOME_ASSISTANT_IP:8098;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

This gives one external hostname with multiple independent pages:

    https://public.fink-holzbau.ch/groundwater/
    https://public.fink-holzbau.ch/outside-temperature/
    https://public.fink-holzbau.ch/whatever/

With `show_index: true`, the root URL lists all enabled Public Sensors pages. With `show_index: false`, the root URL returns 404. Unknown paths always return 404.

If different viewers must have access to different pages, put `auth_basic` inside separate nginx `location` blocks instead of at server level.

## Security

- Do not expose Home Assistant port 8123 through the public hostname.
- Do not port-forward TCP 8098 directly from the Internet.
- Put HTTPS and authentication on nginx.
- If possible, permit TCP 8098 only from internal networks and the reverse proxy.
- Public Sensors uses `homeassistant_api: true` only to read Home Assistant state and Recorder history.
- The Python application makes GET requests only to Home Assistant.
- The browser never receives Home Assistant credentials.
- The graph page has no service-call, POST, PUT, PATCH, or DELETE function.
- Unknown page paths return 404.
- The root path lists only enabled page titles and links when `show_index` is enabled. It does not expose Home Assistant entity IDs.

## Compatibility

Version 0.3.0 can still read the original 0.1.x single-page settings. An existing 0.1.x installation therefore continues to publish its groundwater view at:

    /groundwater/

New configurations should use the `pages` list.

## Troubleshooting

### Page returns 404

Confirm that the configured `path` matches the URL and that `enabled` is true.

### Page returns data unavailable

Open the app log. Check the entity ID and confirm that Recorder contains numeric history for it.

### ON/OFF line stays OFF

The optional binary entity currently maps state `on` to 1 and all other states to 0.

### nginx returns 502

Check the Home Assistant host address and the Public Sensors port mapping in Settings → Apps → Public Sensors → Network.
