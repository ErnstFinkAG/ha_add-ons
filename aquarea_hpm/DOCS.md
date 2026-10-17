# Aquarea HPM Bridge: documentation

This add-on connects a Panasonic Aquarea Heat Pump Manager (PAW-HPM) to Home Assistant.
It reads the HPM's web interface like a browser and publishes the values through MQTT
discovery. An overview with the list of controllable settings is in the add-on's README.

## Requirements

- **MQTT**: the Mosquitto broker add-on (found automatically) or any other broker set with
  `mqtt_host` / `mqtt_port` / `mqtt_username` / `mqtt_password`, and the MQTT integration in
  Home Assistant.
- **Network**: Home Assistant must reach the HPM over HTTP (port 80) on the LAN.
- **Credentials**: the HPM web login password and, for the WEB-FB menus, an access code of a
  service level (Niveau). They are stored only in the add-on options. The add-on never opens
  the menus where codes are shown or changed, and never publishes them.

## Quick start

1. Set `host`, `password` and `access_code`, then start the add-on.
2. Open the add-on's **Web UI**. It shows *Scan running: N pages read so far* and refreshes
   itself. Wait for *Scan finished HH:MM: … values*.
3. Find the device under **Settings → Devices & services → MQTT**.

## Options

### Connection

| Option | Default | Description |
|---|---|---|
| `host` | *(empty, required)* | IP address or hostname of the HPM, optionally with `:port`, e.g. `192.168.1.50`. |
| `username` | *(empty)* | Only if your HPM login page asks for a user name. Most HPMs only ask for a password. |
| `password` | *(empty)* | HPM web login password. Leave empty if the web interface has no login. |
| `access_code` | *(empty)* | Access code entered in WEB-FB under *Zugriffscode eingeben*. It unlocks the deeper menus and is entered again after every new login. |
| `device_name` | `Aquarea HPM` | Name of the device in Home Assistant. It is also used in entity ids and MQTT topics. |

### What is read

| Option | Default | Description |
|---|---|---|
| `include_menus` | `true` | Also walk the WEB-FB menu tree. If off, only the overview pages are read. |
| `menu_values` | `useful` | Which menu values become entities: **`useful`** = current values (Istwerte), status, fault status, hardware/firmware info and the recommended settings (about 170 entities). **`all`** = every menu value (several hundred, mostly installer settings; everything not on the recommended list is read-only). **`none`** = no menu values. |
| `menu_settings` | `true` | Make the recommended menu settings (room setpoints, heating curve, summer switch-off, flow and buffer limits, week programmes, clock, PV/Smart Grid) changeable. Off means read-only. Installer settings are never changeable. |
| `enable_control` | `true` | Master switch for all control entities (overview setpoints, operating mode and menu settings). Off means everything is a sensor. |
| `extra_pages` | `[]` | Extra overview pages to read if your HPM has pages that are not linked, e.g. `v21.rsp`. |
| `max_pages` | `150` | Upper limit of overview pages per scan. |
| `max_menu_pages` | `500` | Upper limit of WEB-FB menu pages per scan (optional). |

### Timing

| Option | Default | Description |
|---|---|---|
| `scan_interval` | `60` | Seconds between reads of the overview pages (current values). |
| `parameter_interval` | `900` | Seconds between reads of the menu pages. Reading all menu pages takes a few minutes, so keep this well above that. |
| `rescan_hours` | `24` | Hours between full rescans that look for new or removed values. The **Rescan values** button starts one immediately. |
| `request_delay` | `0.3` | Pause between two HTTP requests in seconds (optional). The HPM is a small controller, so don't go below 0.2. |

### MQTT and diagnostics

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | *(empty)* | MQTT broker host or IP. Empty = use the Mosquitto broker add-on and its credentials automatically. |
| `mqtt_port` | `1883` | Broker port, used together with `mqtt_host`. |
| `mqtt_username` | *(empty)* | Broker user. With `mqtt_host` empty, it overrides the Mosquitto add-on's own user. |
| `mqtt_password` | *(empty)* | Broker password. |
| `topic_prefix` | `hpm` | Prefix for state and command topics. |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix of Home Assistant. |
| `dump_html` | `false` | Save every page and editor the add-on reads to `/share/aquarea_hpm/` and include them in the debug zip. |
| `log_level` | `info` | `debug` logs every page and HTTP request. |
| `menu_control` | | Old option, no longer used. `menu_settings` replaces it. |

## Entities

| Entity | Type | Notes |
|---|---|---|
| Values from overview pages | sensor | Named after the page and the HPM's descriptive name, e.g. *Anlagenübersicht Aussentemperatur*. Temperatures, percent, kW etc. get a device class and unit. |
| Operating mode (BArtSchalter) | select | Aus, Auto, Sommer, Ferien, Dauer, Hand. |
| Changeable overview values | number | Min/max taken from the HPM editor. |
| Menu values | sensor (*Diagnostic*) | Named after the menu section and label, e.g. *Wärmepumpe 1 Status BStatus*. |
| Menu settings | number / select / text (*Configuration*) | Times (`HH:MM`) and dates (`DD.MM.YY`) are text entities. |
| Connection | binary_sensor | Off when the HPM cannot be reached. |
| Rescan values | button | Starts a full rescan. |

Every entity has these attributes: `hpm_id` (the HPM's parameter number), `page` (where it was
read), `page_title`, `label` (the HPM's short name), `raw` (the text exactly as the HPM shows
it) and `name` (the descriptive name, when the HPM provides one).

### MQTT topics

With `device_name: Aquarea HPM` and default prefixes:

- State: `hpm/aquarea_hpm/<object_id>/state`
- Attributes: `hpm/aquarea_hpm/<object_id>/attributes`
- Command: `hpm/aquarea_hpm/<object_id>/set`
- Availability: `hpm/aquarea_hpm/status` (`online` / `offline`)
- Discovery: `homeassistant/<component>/aquarea_hpm/<object_id>/config`

`object_id` is `dp_` plus the HPM id with separators replaced, e.g. `dp_37_6_4_7` or
`dp_menu_2_5_1_2_1`. Entities that disappear from the HPM (or are filtered out by an option
change) are removed from Home Assistant on the next scan.

## How the add-on talks to the HPM

- **Session.** The HPM puts a `sessionid` in every URL and allows only a few sessions. The
  add-on keeps one session, stores it in `/data` to reuse it after a restart, and logs out when
  it stops. If the HPM shows its *sorry* page (all sessions in use), the add-on waits and
  tries again.
- **Overview pages** (`v0.rsp`, `v10000.rsp`, …): every value link (`vinfo.rsp?id=…`) becomes
  an entity. A value with a set button opens the HPM's editor (`setgrvalue.rsp` →
  `execgrset.rsp`) to learn its type and limits. Opening the editor changes nothing. A set
  button the HPM hides means the value is currently not available.
- **WEB-FB menus** (`webfb.rsp`, `menue.rsp?branchnr=…&level=…`): menu navigation is
  stateful. A link means "sub-menu N of the menu you are in", so the add-on walks the tree
  depth first and checks the menu number on each page before reading it. Parameter rows link
  to `info.rsp`. Changeable rows also have a pencil icon (`change.rsp`), and that is the editor
  used for writing.
- **Never opened**: logout and layout links, access code menus (*Zugriff*, *Zugriffscodes*,
  *Strukturierung / WEB*), system diagrams (*Anlagenschemen*), *Trend*, *Projektmanagement*,
  and menus whose name contains reset, delete, acknowledge, restart or test.

## Web UI and debug zip

The add-on's Web UI shows:

- the scan state (*Scan running …* / *Scan finished …*),
- all values with their HPM id, page, current value and whether they are controllable,
- a **Download debug zip** button. The zip contains the log, a value list (`values.tsv`) and,
  with `dump_html` on, every HPM page and editor from the last scan.

When asking for help, turn on `dump_html` and `log_level: debug`, restart, wait for *Scan
finished* and send the zip. Pages from older scans are deleted at the start of each scan.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *The HPM asks for a login* | Set `password` (and `username` only if the login page has a user field). |
| *No free web session (sorry.rsp)* | Close the HPM web interface in your browser, or wait. The add-on retries by itself. |
| Only overview values, no menu values | `access_code` missing or wrong. The log shows *Entered the access code* when it worked. |
| A setting is a sensor instead of a number | It is not on the recommended list, `menu_settings` is off, or the HPM editor was not understood (the log names the value; send the debug zip). |
| Setting a value has no effect | The HPM refuses values outside its limits, and some settings only apply in certain operating modes. The log shows *Writing …* and any error. |
| Week programmes run at the wrong time | Check *Systemuhr AktZeit / AktDatum*. The HPM clock may be wrong, and you can set it from Home Assistant. |
| Many entities | Keep `menu_values: useful`. With `all`, several hundred installer values appear. |

## Safety notes

- Changing a setting writes directly into the HPM, exactly as if you did it on its web page.
  Note the original values before experimenting.
- Installer settings (terminal assignment, network, sensor correction, controller tuning,
  manual overrides, restarts) are deliberately not changeable through this add-on.
- The add-on never reads or publishes access codes or user names, and the debug zip doesn't
  contain them.
