# Changelog

## 0.1.0

- First version: crawls all HPM overview and menu pages, publishes every value via MQTT
  discovery, and exposes changeable values as number/select entities.
- Not yet verified on a real HPM for writing values; enable `dump_html` and share the files
  from `/share/aquarea_hpm` if a value is missing or cannot be changed.
