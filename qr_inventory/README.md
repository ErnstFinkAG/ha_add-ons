# QR Inventory Add-on

Dieses Add-on nimmt in konfigurierbaren Intervallen Standbilder von einer Kamera (RTSP oder Home Assistant camera entity), erkennt mehrere QR‑Codes, ordnet sie anhand ihrer Position vordefinierten Zonen zu und persistiert bestätigte Zuordnungen erst nach einer konfigurierbaren Anzahl konsistenter Messungen.

## Installation
1. Repository als Add‑on in dein Home Assistant Add‑on Store einfügen oder lokal bauen.
2. Optionen in der Add‑on Konfiguration setzen (RTSP URL oder camera entity, HA Token falls camera entity verwendet wird, Zonen).

## Hinweise
- Zonen sind als Bounding Boxes im Format `[x1,y1,x2,y2]` in Bildpixeln zu definieren.
