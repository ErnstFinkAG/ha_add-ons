## Configure

In the add-on options:

- Set `rtsp_url` to your camera RTSP URL (recommended).
- If `rtsp_url` is set, `camera_entity` is ignored.

Example:
- `rtsp_url: rtsp://user:pass@192.168.1.50:554/stream1`
- `rtsp_transport: tcp`

## Add to Home Assistant as a camera

Using Generic Camera:

```yaml
camera:
  - platform: generic
    name: "QR Tag Overlay"
    still_image_url: "http://<HA-IP>:<PORT>/snapshot.jpg"
    stream_source: "http://<HA-IP>:<PORT>/mjpeg"
```
