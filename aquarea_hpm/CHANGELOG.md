# Changelog

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
