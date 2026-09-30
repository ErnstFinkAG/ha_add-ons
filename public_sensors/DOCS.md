# Public Sensors

## Purpose

Public Sensors publishes selected Home Assistant data on separate read-only URL paths without exposing the Home Assistant frontend.

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

Before the public web server starts, it is launched with a clean environment containing no Supervisor/Home Assistant environment variables and is dropped to the unprivileged `publicsensor` user.

The private `/data` directory and `options.json` are restricted to root. The public process can read the generated cache but cannot write it.

The complete private YAML configuration is never copied into the public cache.

## Universal configuration

Version 0.4.0 removes the previous groundwater-specific configuration.

Each page now contains four fields:

    path
    name
    yaml
    entity_id

Exactly one of `yaml` or `entity_id` must be filled.

Global options remain:

    browser_refresh_seconds
    ha_refresh_seconds
    timezone
    show_index

Example:

    browser_refresh_seconds: 60
    ha_refresh_seconds: 300
    timezone: Europe/Zurich
    show_index: true
    pages:
      - path: groundwater
        name: Grundwasserstand und Pump Verlauf
        entity_id: ""
        yaml: |
          type: custom:apexcharts-card
          header:
            show: true
            title: Grundwasserstand und Pump Verlauf
          graph_span: 48h
          now:
            show: true
          yaxis:
            - id: level
              min: 500
              max: 1000
              apex_config:
                title:
                  text: Wasserstand (mm)
            - id: pump
              min: 0
              max: 1.2
              opposite: true
              apex_config:
                title:
                  text: Pumpe
          apex_config:
            annotations:
              yaxis:
                - y: 660
                  yAxisIndex: 0
                  label:
                    text: Trigger 660 mm
          series:
            - entity: sensor.efimmo_bw_b1_f0_r0_sen0_groundwaterlevel
              name: Wasserstand
              yaxis_id: level
              type: line
              stroke_width: 1
              group_by:
                duration: 5min
                func: avg
            - entity: switch.pumpe_1
              name: Pumpe
              yaxis_id: pump
              type: line
              curve: stepline
              stroke_width: 1

## Page URLs

With the reverse-proxy hostname:

    public.fink-holzbau.ch

the example page is:

    https://public.fink-holzbau.ch/groundwater/

Its public data endpoint is:

    https://public.fink-holzbau.ch/groundwater/data

If `show_index` is true:

    https://public.fink-holzbau.ch/

lists every configured page as a link.

If `show_index` is false, the root URL returns 404 while direct page URLs continue to work.

## YAML mode

Fill `yaml` and leave `entity_id` empty.

The collector parses the YAML with a safe YAML parser. It discovers exact Home Assistant entity-ID strings anywhere in the YAML, including common keys such as:

    entity:
    entity_id:
    entities:

The raw YAML is private and is not sent to the browser.

### ApexCharts-style YAML

The built-in renderer understands the common parts of `custom:apexcharts-card` configuration used for Public Sensors:

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

Numeric history is plotted as numeric data. Non-numeric history is treated as an ON/OFF-style series when used in a graph.

### Other Home Assistant YAML

The configuration field accepts complete YAML, but Public Sensors does not execute Home Assistant frontend cards, JavaScript, Jinja templates, `EVAL`, or custom-card code.

This is intentional. Executing arbitrary Lovelace/custom-card code in an Internet-facing page would weaken the isolation model.

For YAML that contains entity references but no recognized graph series, Public Sensors publishes a generic read-only view of the referenced entity states and attributes.

Additional safe renderers can be added later without changing the page configuration format.

## Entity ID mode

Fill `entity_id` and leave `yaml` empty.

Example:

    - path: outside-temperature
      name: Aussentemperatur
      yaml: ""
      entity_id: sensor.outside_temperature

The collector copies the selected Home Assistant entity state and attributes into the sanitized public cache.

The public page shows:

- configured public name;
- current state;
- unit when present;
- all current entity attributes;
- last update time.

This mode is intended for a simple 1:1 public sensor view.

Because all attributes are published, only use this mode for entities whose attributes are safe to make public. Some Home Assistant entities can contain coordinates, device information, URLs, or other data that should remain private.

## Security properties

The public web server:

- runs as the unprivileged `publicsensor` user;
- has `SUPERVISOR_TOKEN` removed from its environment;
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

This substantially reduces the impact of a compromise of the public Flask/Waitress process. It is still one container and therefore is not equivalent to putting the collector and web server in separate network namespaces. Network/firewall restrictions remain recommended.

## Reverse proxy

The intended deployment is:

    ga.fink-holzbau.ch
        -> Home Assistant :8123

    public.fink-holzbau.ch
        -> Home Assistant host :8098

The public hostname must never proxy to Home Assistant port 8123.

No authentication is required by Public Sensors itself.

Example nginx configuration after the Let's Encrypt certificate exists:

    server {
        listen 80;
        listen [::]:80;
        server_name public.fink-holzbau.ch;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        listen [::]:443 ssl http2;
        server_name public.fink-holzbau.ch;

        ssl_certificate /etc/letsencrypt/live/public.fink-holzbau.ch/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/public.fink-holzbau.ch/privkey.pem;

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

For the current deployment, replace `HOME_ASSISTANT_IP` with the HAOS host IP reachable from the reverse proxy.

Do not forward TCP 8098 directly from the Internet. Permit the reverse proxy to reach it and block unnecessary sources at the firewall where practical.

## Updating from 0.3.x

Version 0.4.0 intentionally removes the old page fields such as:

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

If Home Assistant retains an old 0.3.x options object after updating, reset the Public Sensors configuration to defaults or replace the old `pages` entries with the new four-field structure before starting 0.4.0.

## Troubleshooting

### Collector says exactly one of yaml or entity_id is required

For that page, fill one field and leave the other empty.

### YAML page says no entity references were found

Ensure the YAML contains an entity under `entity`, `entity_id`, or `entities`.

### Public page returns 503

Check the Public Sensors log. The collector may still be starting or the configured Home Assistant entity/YAML may be invalid.

### Public page returns 404

Confirm that the path exists in `pages`. If only the root URL returns 404, check `show_index`.

### nginx returns 502

Verify that the reverse proxy can reach the Home Assistant host on TCP 8098.
