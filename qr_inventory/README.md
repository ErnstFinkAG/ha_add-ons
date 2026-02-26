# QR Inventory Add-on

Dieses Add-on nimmt in konfigurierbaren Intervallen Standbilder von **einer oder mehreren** RTSP/RTSPS‑Kameras, erkennt QR‑Codes, ordnet sie anhand ihrer Position vordefinierten Zonen zu und persistiert bestätigte Zuordnungen erst nach einer konfigurierbaren Anzahl konsistenter Messungen.

## Konfiguration (Multi‑Camera, empfohlen)

Beispiel (in der Add-on Konfiguration / YAML‑Mode):

```yaml
defaults:
  interval_s: 60
  required: 3
  restrict_to_zones: true
  log_level: info

# GLOBAL: Debug für diese Zonennamen (kamerübergreifend)
debug_zones: [A1, A2]

cameras:
  cam1:
    name: "camera1name"
    stream:
      rtsp_url: "rtsp://user:pass@10.0.0.10:554/stream1"
      tls_verify: false
    zones:
      - zone: A1
        rect_px: [120, 180, 980, 720]
      - zone: A2
        rect_px: [1100, 180, 1960, 720]
    settings:
      interval_s: 30
      required: 3
      restrict_to_zones: true
      enabled: true

  cam2:
    name: "camera2name"
    stream:
      rtsp_url: "rtsps://user:pass@10.0.0.11:322/stream1"
    zones:
      - zone: Y1
        rect_px: [100, 120, 900, 420]
      - zone: Y2
        rect_px: [100, 520, 900, 980]
```

### Zonen
- Zonen sind als Bounding Boxes im Format `rect_px: [x1,y1,x2,y2]` in **Bildpixeln** zu definieren.
- Zonennamen sind frei wählbar (z.B. `A1`, `B2`, `Y1` …) und gelten **pro Kamera**.

### Debug
- `debug_zones` ist **global**: wenn z.B. `A1` gesetzt ist, wird Debug für jede Kamera erzeugt, die eine Zone `A1` definiert.
- Sonderfall: `debug_zones: ["*"]` aktiviert Debug für alle Zonen.

## Legacy (Single‑Camera)
Die bisherigen Optionen (`rtsp_url`, `zones` als JSON‑String, `debug_zone`) werden weiterhin unterstützt.
Wenn `cameras` leer ist oder keine Kamera eine `rtsp_url` hat, wird automatisch die Legacy‑Konfiguration verwendet.

## Outputs / Dateien
- Persistierte Zuordnung: `/data/inventory.json` (Mapping `payload -> cam_id.zone`)
- Pro Kamera: `/data/<cam_id>/detections.json`

## HTTP Endpunkte (Port 8099)
- `/` … Index (Links pro Kamera)
- Pro Kamera:
  - `/<cam_id>/overlay.png`
  - `/<cam_id>/frame.png`
  - `/<cam_id>/detections.json`
- Kurz‑Routen:
  - `/overlays/<cam_id>.png`
  - `/frames/<cam_id>.png`
- Aggregat:
  - `/detections.json` (alle Kameras)

### Debug Endpunkte
- `/debug/index.json` … Liste verfügbarer Debug‑Keys (`cam:zone`) + latest pointers
- Pro Debug‑Key:
  - `/debug/<cam_id>/<zone>/debug.json`
  - `/debug/<cam_id>/<zone>/roi.png`
  - `/debug/<cam_id>/<zone>/roi_best.png`
  - `/debug/<cam_id>/<zone>/roi_marked.png`
  - `/debug/<cam_id>/<zone>/roi_best_marked.png`
