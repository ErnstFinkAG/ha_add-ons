# Changelog

## 0.5.5

- When the HPM shows an unexpected page while the add-on moves through the menus, the log
  now says what that page contains (for example the main menu, a "busy" notice or a code
  prompt) instead of only `got ()`. This makes failed menu writes possible to diagnose.

## 0.5.4

- During a full scan the read-only values now appear in Home Assistant as soon as all pages
  are read; the changeable settings follow when their editors have been checked (this
  step takes a few minutes because every menu setting is opened once).
- The web UI shows the progress of every scan step (pages, value names, settings x/y)
  instead of stopping at the page count.

## 0.5.3

- A restart no longer scans the whole HPM web interface again. The last scan is stored in
  `/data` and reused until `rescan_hours` have passed; polling starts right away. A new
  scan still runs after an add-on update, when an option that affects the scan changes,
  and when you press **Rescan values**.

## 0.5.2

- If the MQTT broker is not reachable at start (e.g. Mosquitto still starting after a
  reboot, or a wrong `mqtt_host`/`mqtt_port`), the add-on now waits and retries instead of
  crashing. The log shows which broker address, port and user it uses.

## 0.5.1

- No installation-specific defaults any more: `host` is empty and `device_name` is
  `Aquarea HPM`. Set your own HPM address after installing. Existing installations keep
  their saved options.
- MQTT settings are now visible in the configuration: `mqtt_host`, `mqtt_port`,
  `mqtt_username`, `mqtt_password`. Leave `mqtt_host` empty to use the Mosquitto broker
  add-on automatically; set it to use any other broker. The Mosquitto add-on is no longer
  required.
- Rewritten README and documentation.

## 0.5.0

- New option `menu_values` (default `useful`): only the menu values worth having in Home
  Assistant are published, i.e. current values (Istwerte), status, fault status and the
  recommended settings. `all` publishes every menu value read-only, `none` skips the menus.
- New option `menu_settings` (default on): these recommended menu settings can be changed
  from Home Assistant:
  - Heizkreis: room setpoints (RaumNZ1-4, RaumNN, RaumSNNZ), heating curve slope and
    exponent (HK-Steilh, Hk-Exp), summer switch-off (Aktiv, Aus/EinTemp NZ/NN), min/max
    flow temperature, frost limit, design outdoor temperature, remote override duration
  - Pufferspeicher: boost (ÜberhZ1, Überhöhung), max storage temperature, min off time
  - Wärmepumpe: max setpoint, boost, bivalence point
  - Week programmes of HK1 and Quiet mode (start/end of NZ1/NZ2 per day), special periods
  - HPM clock: time, date, summer/winter switching
  - Photovoltaik and Smart Grid temperatures and times
  Installer settings (terminals, network, sensor correction, controller tuning, manual
  overrides, acknowledgements, restarts) always stay read-only. Values are written through
  the HPM's own edit page (`change.rsp`), the same as clicking the pencil icon.
- `menu_control` is no longer used.

## 0.4.4

- Skips the Trend menus (empty recording buffers, about 580 values) and the
  Projektmanagement list. Home Assistant removes those entities on the next scan.
- Menu value names include their section, e.g. "Wärmepumpe 1 Überhöhung Aktiv" instead of
  "Überhöhung Aktiv" (generic levels like Funktion or Service are left out). Entity ids
  that already exist stay the same.

## 0.4.3

- The add-on page shows whether a scan is still running (and how many pages it has read)
  or when it finished, and refreshes itself while scanning. Download the debug zip after
  "Scan finished".

## 0.4.2

- Each scan starts with an empty debug page folder, so the debug zip only holds pages from
  the current version (an old copy of the WEB menu page with login codes was still in it).

## 0.4.1

- Reads the values on the WEB-FB parameter menus (rows like `Reglertemp   31 °C`), using
  the HPM's own menu numbers as ids (e.g. 1.3.1.2), and polls them every
  `parameter_interval`. They are read-only for now.
- Checks the menu position with the "Menü 1.3.4.x" number on each page instead of the
  menu name, so the walk no longer retries every page (and is about three times faster).
- Never opens the Strukturierung / WEB menu and never publishes rows like "Niveau" or
  "Benutzer", so web login codes stay out of Home Assistant and out of the debug zip.
- Empty rows (unused slots, empty fault texts) are skipped.
- New optional `max_menu_pages` (default 500) limits the menu walk separately.

## 0.4.0

- The WEB-FB menus are now walked step by step the way a person clicks through them
  (down into a sub-menu, back up via the menu header). The HPM remembers the current menu
  position, so the 0.3.0 approach of opening menu links directly kept landing on the same
  page and found no values.
- Menu values are named after their menu (e.g. "Störstatus") and the dump files are called
  `page_menu_1_3_1.html` (path of menu numbers from the main menu).
- Besides the access-code menus, "Anlagenschemen" and menus with reset/delete/test in their
  name are never opened.

## 0.3.0

- Reads the WEB-FB menu tree (`webfb.rsp` → `menue.rsp?branchnr=…&level=…`), so the
  parameters behind the HPM's menus become sensors. Menus are read every
  `parameter_interval`.
- If `access_code` is set (the code of a service level), it is entered through WEB-FB's
  "Zugriffscode eingeben" before the menus are read, and again after every re-login.
- The Zugriffscodes menu itself is never opened, so codes don't end up in Home Assistant.
- A page error (404) no longer throws away the session (and the entered code).
- Menu parameters are read-only unless the new `menu_control` option is turned on.
- Replaces the 0.2.2 WEB-FB start-page snapshot (all menu pages are in the debug zip now).

## 0.2.2

- With `dump_html` on, the WEB-FB start page (the HPM's display/menu emulation) is saved
  read-only into the debug zip as `webfb_start.html`, to check whether the menu can be read
  safely. None of its buttons are pressed.

## 0.2.1

- Names come from the HPM's "Parameterinformationen" (Langtext), e.g. "Heizkreis 1
  Aussentemperatur", "Heizkreis 1 Vorlauftemperatur", "Pufferspeicher Puffertemperatur oben".
- The "Status" links that only open a status page are no longer published as sensors.

## 0.2.0

Based on the pages of a real HPM (schema 10130):

- Changeable setpoints now work: the editor (`execgrset.rsp`) with its German table
  (Bisheriger Wert / Untergrenze / Obergrenze) becomes a number entity with the HPM's limits.
- The operating mode switch on the overview page (BArtSchalter: Aus, Auto, Sommer, Ferien,
  Dauer, Hand) is now a select entity.
- Entities get the HPM's descriptive names (e.g. "Heizkreis 1 Sollwert NZ1") from the
  parameter and info pages instead of the whole page text.
- Values the HPM hides as currently unavailable are skipped.

## 0.1.3

- Status page in Home Assistant (add-on → Open Web UI, or the sidebar) listing every value.
- "Download debug zip" button with the log, the value list and the saved HPM pages, so no
  Samba or file editor add-on is needed to share them.

## 0.1.2

- New `access_code` option: entered automatically when a deeper menu asks for a code after
  login. Menus that ask for a code without one configured are skipped instead of causing
  re-logins.

## 0.1.1

- Log out of the HPM when the add-on stops and re-use the previous session after a restart,
  so restarts no longer end on `sorry.rsp` (all HPM web sessions in use).
- Clear message and back-off when the HPM has no free session.
- Find pages that are linked through JavaScript or frames, not only plain links.
- Better names for changeable values that have no label in their own link.

## 0.1.0

- First version: crawls all HPM overview and menu pages, publishes every value via MQTT
  discovery, and exposes changeable values as number/select entities.
- Not yet verified on a real HPM for writing values; enable `dump_html` and share the files
  from `/share/aquarea_hpm` if a value is missing or cannot be changed.
