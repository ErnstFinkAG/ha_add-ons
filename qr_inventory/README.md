# QR Inventory Add-on

Dieses Add-on nimmt in konfigurierbaren Intervallen Standbilder von **einer oder mehreren** RTSP/RTSPS-Kameras, erkennt QR-Codes, ordnet sie Zonen zu und stellt Live-Daten per HTTP und MQTT bereit.

## Wichtige Funktionen

- Mehrere Kameras
- MQTT Discovery + MQTT Zonensensoren
- Polygon-Zonen mit 4 Ecken oder klassische Rechtecke
- Alignment-Linien und Margin-Rahmen im Overlay
- Live-Liste erkannter Payloads mit Gruppierung, Regex und Sortierung
- Druckansichten für alle Projekte oder einzelne Projekte
- Optional mehrere QR-Codes pro Zone
- Retention/Bestätigung über `required`

## Kamera-Konfiguration

Die Home-Assistant Add-on UI unterstützt nur begrenzt tief verschachtelte Strukturen. Darum werden Zonen pro Kamera als **JSON-String** definiert.

Beispiel:

```yaml
defaults:
  interval_s: 60
  required: 3
  restrict_to_zones: true
  log_level: info

mqtt:
  enabled: true
  host: core-mosquitto
  port: 1883
  username: mqtt_user
  password: mqtt_pass
  client_id: qr_inventory
  topic_prefix: qr_inventory
  discovery_prefix: homeassistant
  retain: true
  keepalive: 60

cameras:
  - id: cam1
    name: "camera1name"
    rtsp_url: "rtsp://user:pass@10.0.0.10:554/stream1"
    tls_verify: false
    zones: '{"A1":[120,180,980,720],"A2":[[1100,180],[1960,200],[1930,760],[1080,740]]}'
    interval_s: 30
```

## Zonen

Unterstützte Formate pro Zone:

Klassisches Rechteck:
```json
{"D2": [120, 180, 980, 720]}
```

Polygon mit 4 Ecken:
```json
{"D2": [[120,180],[980,160],[1010,720],[140,760]]}
```

## Overlay-Helfer

```yaml
overlay_alignment_enabled: true
overlay_alignment_color: FFFFFF
overlay_alignment_direction: both
overlay_alignment_width: 2
overlay_margin_enabled: true
overlay_margin_px: 10
```

- `overlay_alignment_direction`: `horizontal`, `vertical` oder `both`
- Linien und Margin-Box wirken nur auf das Overlay, nicht auf die Erkennung

## MQTT

Jede definierte Zone wird als Sensor veröffentlicht.

- Sensorname: `cameraname_zonename`
- kein QR → `none`
- erkannt, aber nicht dekodierbar → `detected_no_value`
- ein QR → Payload
- mehrere QRs → Payloads verbunden mit `zone_multi_value_separator`

Konfiguration:

```yaml
mqtt:
  enabled: true
  host: core-mosquitto
  port: 1883
  username: mqtt_user
  password: mqtt_pass
  client_id: qr_inventory
  topic_prefix: qr_inventory
  discovery_prefix: homeassistant
  retain: true
  keepalive: 60

zone_multi_value_separator: " | "
```

Topics:
- `qr_inventory/status`
- `qr_inventory/zones/<cam_id>/<zone>/state`
- `qr_inventory/zones/<cam_id>/<zone>/attributes`
- `homeassistant/sensor/<client_id>/<sensor>/config`

## Live-Liste erkannter Payloads

```yaml
detected_list_enabled: true
detected_list_regex: "(\d{6})"
detected_list_sort_order: asc
```

- `detected_list_regex`: extrahiert den Gruppierungsschlüssel aus der Payload
- `detected_list_sort_order`: `asc` oder `desc`
- basiert auf **live** propagierten Werten, nicht auf persistenter Historie

## Performance / Parallelisierung

```yaml
zone_worker_processes: 0
zone_parallel_min_zones: 3
```

- `zone_worker_processes`: `0` = Auto, `1` = seriell, `N` = feste Anzahl Worker-Prozesse **pro Kamera**
- `zone_parallel_min_zones`: ab wie vielen definierten Zonen die Parallelisierung aktiviert wird
- Die Kamera erfasst weiterhin **einen** Frame pro Zyklus und verteilt danach die Zonen-Scans auf Worker-Prozesse
- Debug-Zonen laufen absichtlich seriell, damit die Debug-Bilder erhalten bleiben

HTTP:
- `/detected-list.json`
- `/detected-list` (HTML-Suchseite)
- `/print`
- `/print/all`
- `/print/project/<group_key>`

## Retention / Bestätigung

Die Propagierung von Zonenzuständen folgt dem vorhandenen `required`-Wert pro Kamera.
Ein Wert wird erst aktiv, wenn er `required` Zyklen konsistent erkannt wurde.
Das gilt auch für:
- Rücksetzung auf `none`
- `detected_no_value`
- Mehrfachwerte in einer Zone

## Lovelace Custom Card

Die Suchkarte liegt als separate Datei bei:
- `custom_cards/qr_inventory_search_card.js`

Einbindung als Dashboard-Ressource:
```yaml
type: custom:qr-inventory-search-card
title: QR Inventory
entity: sensor.qr_inventory_detected_list
print_base_url: http://YOUR_ADDON_HOST:8099
```
