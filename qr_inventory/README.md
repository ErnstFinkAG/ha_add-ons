# QR Inventory Add-on

Dieses Add-on nimmt in konfigurierbaren Intervallen Standbilder von **einer oder mehreren** RTSP/RTSPS‑Kameras, erkennt QR‑Codes, ordnet sie anhand ihrer Position vordefinierten Zonen zu und persistiert bestätigte Zuordnungen erst nach einer konfigurierbaren Anzahl konsistenter Messungen.

## Konfiguration (Multi‑Camera)

> Hinweis: Die Home‑Assistant Add-on UI unterstützt nur begrenzt tief verschachtelte Strukturen. Darum werden Zonen pro Kamera als **JSON‑String** definiert (Mapping `ZONENAME -> [x1,y1,x2,y2]`).

Beispiel (in der Add-on Konfiguration / YAML‑Mode):

```yaml
defaults:
  interval_s: 60
  required: 3
  restrict_to_zones: true
  log_level: info

# GLOBAL: Debug für diese Zonennamen (kamerübergreifend)
# Use ["*"] für alle Zonen
debug_zones: [A1, A2]

cameras:
  - id: cam1
    name: "camera1name"
    rtsp_url: "rtsp://user:pass@10.0.0.10:554/stream1"
    tls_verify: false
    # JSON string: {"A1":[x1,y1,x2,y2], "A2":[...]}
    zones: '{"A1":[120,180,980,720],"A2":[1100,180,1960,720],"B1":[120,980,980,1580]}'
    interval_s: 30

  - id: cam2
    name: "camera2name"
    rtsp_url: "rtsps://user:pass@10.0.0.11:322/stream1"
    zones: '{"Y1":[100,120,900,420],"Y2":[100,520,900,980]}'
    required: 5
```

### Zonen
- Zonen sind als Bounding Boxes im Format `[x1,y1,x2,y2]` in **Bildpixeln** zu definieren.
- Zonennamen sind frei wählbar (z.B. `A1`, `B2`, `Y1` …) und gelten **pro Kamera**.

### Debug
- `debug_zones` ist **global**: wenn z.B. `A1` gesetzt ist, wird Debug für jede Kamera erzeugt, die eine Zone `A1` definiert.
- Sonderfall: `debug_zones: ["*"]` aktiviert Debug für alle Zonen.

## HTTP Endpoints

- `/{cam_id}/overlay.png` – aktuelles Overlay (falls verfügbar)
- `/{cam_id}/frame.png` – letzter Frame (falls verfügbar)
- `/{cam_id}/detections.json` – Detektionen dieser Kamera
- `/detections.json` – aggregiert über alle Kameras
- `/{overlay_route_prefix}/{cam_id}.png` – Shortcut (Default: `/overlays/<cam_id>.png`)
- `/{frame_route_prefix}/{cam_id}.png` – Shortcut (Default: `/frames/<cam_id>.png`)
