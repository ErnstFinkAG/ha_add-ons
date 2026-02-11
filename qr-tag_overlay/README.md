# QR Tag Overlay (RTSP)

This add-on grabs frames either from:
- **RTSP** (recommended), or
- a Home Assistant `camera.*` entity via `camera_proxy`

It decodes QR codes with **ZBar** and overlays the last decoded text on the image.

## Endpoints
- `/snapshot.jpg` annotated still
- `/mjpeg` annotated MJPEG stream
- `/status` JSON (source + last decoded text)
