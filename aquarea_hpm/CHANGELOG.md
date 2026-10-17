# Changelog

## 0.3.0

- Reads the WEB-FB menu tree (`webfb.rsp` → `menue.rsp?branchnr=…&level=…`), so the
  parameters behind the HPM's menus become sensors. Menus are read every
  `parameter_interval`.
- Access-code menus (Zugriffscodes, Zugriffscode eingeben) are never opened.
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
