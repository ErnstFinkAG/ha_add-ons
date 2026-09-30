# Public Sensors

## Purpose

Public Sensors publishes selected Home Assistant data on separate, read-only URL paths without exposing the Home Assistant frontend.

The public web process does not receive the Home Assistant Supervisor token. Home Assistant access is isolated in a private collector process.

## Architecture

The app contains two processes:

    Home Assistant Core
          ^
          | internal API + SUPERVISOR_TOKEN
          |
    private collector
          |
          | sanitized JSON cache
          v
    unprivileged public web process :8098
          |
          v
    reverse proxy / Internet

The collector is the only process that receives `SUPERVISOR_TOKEN`.

The public web server is started with a clean environment containing no Supervisor/Home Assistant environment variables and runs as the unprivileged `publicsensor` user.

The private `/data` directory and `options.json` are restricted to root. The public process can read the generated cache but cannot write it.

The complete private YAML configuration is never copied into the public cache.

## First start

The repository default intentionally contains no sensor pages:

    browser_refresh_seconds: 60
    ha_refresh_seconds: 300
    timezone: UTC
    show_index: true
    pages: []

This makes a fresh installation independent of the repository maintainer's Home Assistant entities.

With `show_index: true`, the root page starts normally and shows that no public sensor pages are configured yet.

Set `timezone` to your local IANA timezone, for example:

    Europe/Zurich
    Europe/London
    America/New_York

## Universal page configuration

Each public page contains four fields:

    path
    name
    yaml
    entity_id

Exactly one of `yaml` or `entity_id` must be filled.

### Simple entity example

    pages:
      - path: outside-temperature
        name: Outside temperature
        yaml: ""
        entity_id: sensor.outside_temperature

The public page is then:

    https://public.example.com/outside-temperature/

### YAML example

    pages:
      - path: climate-history
        name: Climate history
        entity_id: ""
        yaml: |
          type: custom:apexcharts-card
          header:
            show: true
            title: Climate history
          graph_span: 48h
          now:
            show: true
          yaxis:
            - id: temperature
              min: -20
              max: 45
              apex_config:
                title:
                  text: Temperature
          series:
            - entity: sensor.outside_temperature
              name: Outside temperature
              yaxis_id: temperature
              type: line
              stroke_width: 1
              group_by:
                duration: 5min
                func: avg

## Page URLs

For a reverse-proxy hostname such as:

    public.example.com

a configured page with:

    path: outside-temperature

is available at:

    https://public.example.com/outside-temperature/

Its public data endpoint is:

    https://public.example.com/outside-temperature/data

If `show_index` is true:

    https://public.example.com/

lists every configured page.

Every public sensor page also shows a `← Übersicht` button that returns to the root index.

If `show_index` is false, the root URL returns 404, direct page URLs continue to work, and the return-to-index button is not rendered.

## YAML mode

Fill `yaml` and leave `entity_id` empty.

The collector parses the YAML with a safe YAML parser and discovers exact Home Assistant entity-ID strings anywhere in the YAML.

The raw YAML remains private and is not sent to the browser.

### ApexCharts-style YAML

The built-in renderer understands the common parts of `custom:apexcharts-card` configuration used by Public Sensors:

- `graph_span`
- `now.show`
- `yaxis`
- Y-axis min/max/opposite/title
- `series`
- series name
- `yaxis_id`
- `type`
- `curve: stepline`
- `stroke_width`
- `group_by.duration`
- `group_by.func` with avg, min, max, last, or sum
- Y-axis annotations

Numeric history is plotted as numeric data. Non-numeric history can be rendered as an ON/OFF-style step series.

### Other Home Assistant YAML

The configuration field accepts complete YAML, but Public Sensors does not execute Home Assistant frontend cards, JavaScript, Jinja templates, `EVAL`, or arbitrary custom-card code.

For YAML that contains entity references but no recognized graph series, Public Sensors publishes a generic read-only view of the referenced entity states and attributes.

Additional safe renderers can be added later without changing the page configuration format.

## Entity ID mode

Fill `entity_id` and leave `yaml` empty.

The collector copies the selected Home Assistant entity state and attributes into the sanitized public cache.

The public page shows:

- configured public name;
- current state;
- unit when present;
- current entity attributes;
- last update time.

Because all attributes are published, only use this mode for entities whose attributes are safe to make public. Some Home Assistant entities can contain coordinates, device information, URLs, or other data that should remain private.

## Security properties

The public web server:

- runs as the unprivileged `publicsensor` user;
- receives no Supervisor/Home Assistant environment variables;
- does not import the Home Assistant collector code;
- cannot read `/data/options.json`;
- can only read the root-owned sanitized cache;
- accepts only GET and HEAD;
- has no service-call endpoint;
- has no upload endpoint;
- has no generic proxy endpoint;
- does not expose the raw YAML configuration.

The collector:

- is not exposed on a TCP port;
- holds the Supervisor token;
- performs only GET requests to Home Assistant;
- writes the sanitized cache atomically;
- never writes the Supervisor token to the cache.

This reduces the impact of a compromise of the public Flask/Waitress process. It is still one container and is not equivalent to separate network namespaces. Network and firewall restrictions remain recommended.

## Reverse proxy

Keep Home Assistant and Public Sensors separate:

    ha.example.com
        -> HOME_ASSISTANT_IP:8123

    public.example.com
        -> HOME_ASSISTANT_IP:8098

The public hostname must never proxy to Home Assistant port 8123.

Example nginx configuration after the TLS certificate exists:

    server {
        listen 80;
        listen [::]:80;
        server_name public.example.com;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        listen [::]:443 ssl http2;
        server_name public.example.com;

        ssl_certificate /etc/letsencrypt/live/public.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/public.example.com/privkey.pem;

        ssl_protocols TLSv1.2 TLSv1.3;

        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;

        location = /healthz {
            return 404;
        }

        location / {
            limit_except GET HEAD {
                deny all;
            }

            proxy_pass http://HOME_ASSISTANT_IP:8098;
            proxy_http_version 1.1;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

Do not forward TCP 8098 directly from the Internet. Permit the reverse proxy to reach it and block unnecessary sources at the firewall where practical.

## Updating from 0.3.x

Version 0.4 removes the old page fields such as:

    sensor_entity
    sensor_name
    sensor_unit
    sensor_min
    sensor_max
    binary_entity
    history_hours
    group_minutes
    threshold_value

The new configuration is universal and does not keep the old single-purpose schema.

If Home Assistant retains an old 0.3.x options object after updating, reset Public Sensors to defaults or replace the old `pages` entries with the new four-field structure.

## Troubleshooting

### No pages are shown after installation

This is the intended default. Add one or more entries under `pages`.

### Collector says exactly one of yaml or entity_id is required

For that page, fill one field and leave the other empty.

### YAML page says no entity references were found

Ensure the YAML contains at least one valid Home Assistant entity ID.

### Public page returns 503

Check the Public Sensors log. The collector may still be starting or the configured Home Assistant entity/YAML may be invalid.

### Public page returns 404

Confirm that the path exists in `pages`. If only the root URL returns 404, check `show_index`.

### nginx returns 502

Verify that the reverse proxy can reach the Home Assistant host on TCP 8098.
