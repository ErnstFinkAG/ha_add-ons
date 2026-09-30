# Public Sensors

## Purpose

Public Sensors publishes selected Home Assistant history on a separate, read-only web page. It is intended for cases where a graph must be shared without exposing the Home Assistant frontend to the viewer.

The app runs on Home Assistant OS as a separate container. It reads Home Assistant Core through the internal Supervisor proxy and serves its own web interface on TCP port 8098.

The browser receives only the HTML/CSS/JavaScript for the graph and the selected history data from /api/data.

The browser does not receive SUPERVISOR_TOKEN, a Home Assistant long-lived token, or a Home Assistant service endpoint.

## Default groundwater configuration

Version 0.1.0 uses these defaults:

    title: Grundwasserstand und Pump Verlauf
    sensor_entity: sensor.efimmo_bw_b1_f0_r0_sen0_groundwaterlevel
    sensor_name: Wasserstand
    sensor_unit: mm
    sensor_min: 500
    sensor_max: 1000
    binary_entity: switch.pumpe_1
    binary_name: Pumpe
    history_hours: 48
    group_minutes: 5
    threshold_value: 660
    threshold_label: Trigger 660 mm
    browser_refresh_seconds: 60
    ha_refresh_seconds: 300
    timezone: Europe/Zurich

The numeric history is averaged into five-minute buckets. The ON/OFF entity is drawn as a step line and keeps its state changes.

## Installation

If this repository is already configured in Home Assistant, refresh the app/add-on store after the new version is merged.

For a new installation:

1. Open Settings → Apps → App store.
2. Open the repository menu.
3. Add https://github.com/ErnstFinkAG/ha_add-ons
4. Refresh the store.
5. Install Public Sensors.
6. Open its Configuration page and verify the entity IDs.
7. Open its Network page and keep TCP 8098 mapped to an unused host port. The default is 8098.
8. Start the app.
9. Check the log. A successful refresh contains a message similar to:

       History refreshed: 577 numeric points

10. Open the web UI from Home Assistant or browse to:

       http://HOME_ASSISTANT_IP:8098/

Do not forward TCP 8098 directly from the Internet.

## Configuration

### title

Title shown above the graph.

### sensor_entity

Numeric Home Assistant entity whose Recorder history is shown as the main graph.

### sensor_name

Display name for the numeric series.

### sensor_unit

Unit shown on the graph. Set this to an empty string to use the entity's Home Assistant unit_of_measurement attribute.

### sensor_min and sensor_max

Fixed left-axis range.

### binary_entity

Optional ON/OFF entity. The default is switch.pumpe_1. Set it to an empty string if no binary series is required.

States equal to on are drawn as 1. Other states are drawn as 0.

### binary_name

Display name for the ON/OFF series.

### history_hours

History range requested from Home Assistant Recorder. Default: 48.

### group_minutes

Size of the averaging bucket for the numeric history. Default: 5.

### threshold_value

Value of the horizontal threshold line.

### threshold_label

Text shown next to the threshold line.

### browser_refresh_seconds

How often an open browser requests the app's cached data. Default: 60 seconds.

### ha_refresh_seconds

How often the app refreshes history from Home Assistant. Default: 300 seconds.

The browser does not make Home Assistant history requests directly.

### timezone

IANA timezone used for graph labels, for example Europe/Zurich.

## HTTP endpoints

The app intentionally has a small HTTP surface:

| Method | Path | Purpose |
| --- | --- | --- |
| GET / HEAD | / | Read-only graph page |
| GET / HEAD | /api/data | Cached graph data |
| GET / HEAD | /static/app.js | Local graph JavaScript |
| GET / HEAD | /static/style.css | Local stylesheet |
| GET / HEAD | /healthz | Local health check |

POST, PUT, PATCH, DELETE, and other write methods return HTTP 405.

There is no endpoint for calling Home Assistant services or changing an entity.

## Recommended nginx design

Keep normal Home Assistant access and Public Sensors on separate virtual hosts:

    ga.example.com
        -> HOME_ASSISTANT_IP:8123

    gwl.example.com
        -> HOME_ASSISTANT_IP:8098

The gwl host must not proxy to Home Assistant port 8123.

A restrictive nginx example is:

    server {
        listen 80;
        server_name gwl.example.com;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name gwl.example.com;

        ssl_certificate /etc/letsencrypt/live/gwl.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/gwl.example.com/privkey.pem;

        ssl_protocols TLSv1.2 TLSv1.3;

        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "no-referrer" always;

        auth_basic "Public Sensors";
        auth_basic_user_file /etc/nginx/.htpasswd-public-sensors;

        location = / {
            proxy_pass http://HOME_ASSISTANT_IP:8098;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location = /api/data {
            proxy_pass http://HOME_ASSISTANT_IP:8098;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location = /static/app.js {
            proxy_pass http://HOME_ASSISTANT_IP:8098;
            proxy_set_header Host $host;
        }

        location = /static/style.css {
            proxy_pass http://HOME_ASSISTANT_IP:8098;
            proxy_set_header Host $host;
        }

        location / {
            return 404;
        }
    }

Create a password file on the nginx host, for example:

    sudo htpasswd -c /etc/nginx/.htpasswd-public-sensors groundwater

Then test and reload nginx:

    sudo nginx -t
    sudo systemctl reload nginx

For the current deployment the intended public host can be gwl.fink-holzbau.ch, while normal Home Assistant access remains on ga.fink-holzbau.ch.

## Security notes

- Do not expose Home Assistant port 8123 through the Public Sensors virtual host.
- Do not port-forward the app port directly from the Internet.
- Put HTTPS and authentication on the reverse proxy.
- If possible, use the network firewall to allow access to the app port only from trusted internal networks and the reverse proxy.
- The app uses homeassistant_api: true because it must read Recorder history through the internal Home Assistant API.
- SUPERVISOR_TOKEN stays inside the app container. It is never included in page HTML or /api/data.
- The supplied application code performs only HTTP GET requests against Home Assistant.
- The page has no controls that call Home Assistant services.
- A visitor can modify the appearance of a page in their own browser developer tools, but that does not modify Home Assistant, the cached source data, or what another visitor sees.

## Troubleshooting

### The page says that data could not be loaded

Open the app log. Common causes are:

- incorrect entity ID;
- Recorder has no history for the selected numeric entity;
- the entity state is not numeric;
- Home Assistant Core API access is not available.

### The graph has no old data

Check that Recorder stores the entity and that history_hours does not request data older than your Recorder retention.

### The pump line stays OFF

Check that binary_entity uses on and off states. Version 0.1.0 maps on to 1 and all other states to 0.

### nginx returns 502

Check the Home Assistant host IP and the mapped Public Sensors port in Settings → Apps → Public Sensors → Network.
