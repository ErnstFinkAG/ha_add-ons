# Aquarea HPM Bridge

Home Assistant add-on for the **Panasonic Aquarea Heat Pump Manager** (PAW-HPM, HPM1/HPM2;
the device often appears on the network as `HPM-xxxxxx`).

The HPM is the controller that sits between an Aquarea heat pump (for example
an Aquarea T-CAP or J/K series), the buffer tank and the heating circuits. It has
a built-in web interface but no API. This add-on reads that web interface the same way a
person would in a browser and turns what it finds into Home Assistant entities through MQTT
discovery: sensors for current values and status, and number/select/text entities for the
settings you may want to program yourself.

![type](https://img.shields.io/badge/type-MQTT%20bridge-blue) ![arch](https://img.shields.io/badge/arch-aarch64%20%7C%20amd64%20%7C%20armv7-green)

---

## What you get

| Area | Examples | Entity type |
|---|---|---|
| **Overview pages** (Anlagenübersicht, Wärmeerzeuger, Pufferspeicher, Heizkreis) | outdoor temperature, buffer temperature, flow temperature, compressor frequency, heat pump release, fault messages | sensor |
| **Operating mode** | BArtSchalter (Aus / Auto / Sommer / Ferien / Dauer / Hand) | select |
| **Overview setpoints** | room setpoints NZ1, NZ2, NN shown on the overview page | number (with the HPM's min/max) |
| **Current values from the menus** (Istwerte, Status) | heat pump, buffer, heating circuit and PV/Smart Grid status, active time slot, fault status, controller temperature, battery voltage, firmware | sensor |
| **Recommended settings from the menus** | room setpoints NZ1–4/NN/SNNZ, heating curve, summer switch-off, flow limits, buffer limits, week programmes, HPM clock … (full list below) | number / text |
| **Add-on itself** | Connection (connected/disconnected), Rescan values button | binary_sensor, button |

All entities belong to one device (named after `device_name`) with a link to the HPM web
interface. Menu values are grouped as *Configuration* (settings) and *Diagnostic*
(read-only), so they don't clutter the default dashboard.

---

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add
   `https://github.com/ErnstFinkAG/ha_add-ons`.
2. Install **Aquarea HPM Bridge**.
3. Make sure an MQTT broker is running and the **MQTT** integration is set up. With the
   **Mosquitto broker** add-on nothing else is needed: the add-on finds it by itself. For any
   other broker set `mqtt_host`, `mqtt_port`, `mqtt_username` and `mqtt_password`.
4. Open the add-on's **Configuration** tab and set at least:
   - `host`: IP address or hostname of the HPM (e.g. `192.168.1.50`)
   - `password`: the HPM web login password (leave empty if your HPM has no login)
   - `access_code`: the HPM access code for the service level (Niveau) that unlocks the
     deeper WEB-FB menus. Without it you only get the overview pages.
5. Start the add-on and open its **Web UI** (also available in the sidebar). Wait until it
   says **Scan finished**. The first scan walks the whole menu tree and takes a few minutes.
6. The entities appear under **Settings → Devices & services → MQTT → Aquarea HPM**
   (or whatever you set as `device_name`).

---

## How it works

```
 Home Assistant ◄── MQTT discovery ──  Aquarea HPM Bridge  ── HTTP (like a browser) ──►  HPM web UI
```

1. **Login.** Opens the HPM start page, signs in with `password` (and `username` if your HPM
   asks for one) and keeps that web session. The session is reused after a restart and
   released when the add-on stops, because the HPM only allows a few sessions at once.
2. **Overview pages.** Follows every overview page (`v0.rsp`, `v10000.rsp`, …) and reads
   each value. Each value's info page gives its descriptive name, and its editor page shows
   whether it is a number (with min/max) or a list.
3. **WEB-FB menus.** Enters the access code, then clicks through the WEB-FB menu tree step by
   step, the same way a person would. The HPM remembers which menu a session is in, so the add-on moves
   down into sub-menus and back up through the menu header, and checks the menu number
   ("Menü 2.5.1.2.x") on every page. Menus that hold access codes, the system diagrams, Trend
   recordings and project management are **never opened**.
4. **Filter.** With the default `menu_values: useful` only current values, status and the
   recommended settings are published (see below). Everything else stays out of Home Assistant.
5. **Polling.** Overview pages are read every `scan_interval` (60 s), menu pages every
   `parameter_interval` (15 min), and a full rescan runs every `rescan_hours` (24 h) or when
   you press **Rescan values**.
6. **Changing a value** repeats what you would do in the browser: open the value's editor
   (or the pencil icon in a menu), enter the new value, press OK. Numbers outside the min/max
   shown by the HPM are refused before anything is sent.

Each entity keeps the HPM's own parameter number as its unique id (for example
`37:6.4.7` or `menu:2.5.1.2.1`), so renaming in Home Assistant or a changed label on the HPM
does not lose history.

---

## Recommended settings you can change

With `menu_settings: true` (default) these menu settings become controllable. Names in
Home Assistant are built from the menu section and the HPM's short label, e.g.
*Heizkreis 1 Sollwerte RaumNZ1*.

### Heating circuit (Heizkreis 1)

| HPM label | Meaning | Why you might change it |
|---|---|---|
| RaumNZ1 … RaumNZ4 | Room setpoint for usage times (Nutzungszeit) 1–4 | Comfort temperature during the day |
| RaumNN | Room setpoint for non-usage time (Nichtnutzung, night setback) | Night / absence temperature |
| RaumSNNZ | Room setpoint for special periods | Holiday / special programme |
| HK-Steilh | Heating curve slope | Main tuning knob: higher means warmer flow when it is cold outside |
| Hk-Exp | Heating curve exponent | Curve shape (radiators about 1.3, floor heating about 1.1) |
| Sommerabschaltung Aktiv | Summer switch-off on/off | Stop heating automatically in warm weather |
| AusTempNZ / AusTempNN | Outdoor temperature above which heating stops (day / night) | Seasonal fine-tuning |
| EinTempNZ / EinTempNN | Outdoor temperature below which heating starts again | Seasonal fine-tuning |
| minVL / maxVL | Minimum / maximum flow temperature | Protect floor heating, limit energy use |
| AussenFrGr | Frost protection outdoor limit | Frost protection |
| AuslAT / GrAussen | Design outdoor temperature | Heating curve end point |
| DauÜberstd | Duration of a remote "extra hours" request | Party / override length |

### Buffer tank (Pufferspeicher) and heat pump (Wärmepumpe 1)

| HPM label | Meaning |
|---|---|
| ÜberhZ1 | Buffer boost above the heating circuit demand |
| Überhöhung Aktiv / Überh.HK1 | Boost function on/off and amount |
| maxTSpei | Maximum buffer temperature |
| minAus | Minimum off time of the buffer demand |
| maxSW | Maximum setpoint the heat pump is asked for |
| Überhöhung Aktiv / Überh.HK | Heat pump boost on/off and amount |
| Bivalenz Grenze | Bivalence point (outdoor temperature for the second heat source) |

### Time programmes and clock

| Setting | Meaning |
|---|---|
| Wochenprogramm *Mo … So* `Anz NZ`, `Beg NZ1`, `End NZ1`, `Beg NZ2`, `End NZ2` | Week programme of Heizkreis 1 and of Quiet mode: number of usage periods and start/end time per day (`HH:MM`, `--:--` = unused) |
| Sonder-Nichtn. / Sonder-Nutzungsz. (AnzSNN, AnzSNZ) | Number of special non-usage / usage periods |
| Systemuhr AktZeit / AktDatum | HPM clock (`HH:MM`, `DD.MM.YY`). **Check this first**: if the clock is wrong, the week programmes run at the wrong times. |
| ArtSoWiUmsch | Automatic summer/winter time switching |

### Photovoltaik and Smart Grid

PV demand temperature (Anf-HK), minimum off time (MinAusZ) and the Smart Grid
operating-state temperatures and boosts. These are only useful if a PV or SG-ready
signal is wired to the HPM.

### Always read-only

Terminal assignments (Klemmen), network settings, sensor corrections, controller tuning
(Xp, Tn …), manual overrides (Handsteuerung), acknowledgements, cold/warm start, language
and everything under access codes. These are installer settings. Change them on the HPM itself
if you really need to.

---

## Good to know

- **Use with care.** Settings written from Home Assistant go straight into the HPM. Write
  down the original values before experimenting. The previous value is also kept in the
  entity's attributes (`raw`).
- **Only a few web sessions.** The HPM allows only a few parallel web logins. If the add-on
  reports *busy*, close the HPM page in your browser. Don't set `request_delay` or the
  intervals much lower than the defaults.
- **Scan time.** A full scan reads about 300 menu pages and takes 2–4 minutes. Values update
  during polling; the scan itself only runs at start, every `rescan_hours` and on request.
- **Other HPM installations** (other schemas, more heating circuits or tanks) should work:
  the add-on discovers what it finds. The recommended-settings list matches menu names
  such as *Heizkreis 2* as well.

See the **Documentation** tab (DOCS.md) for every option, the MQTT topics and
troubleshooting.
