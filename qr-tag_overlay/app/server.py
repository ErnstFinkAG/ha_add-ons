import asyncio
import io
import os
import time
from typing import Optional, Tuple

from aiohttp import web, ClientSession, ClientTimeout
from PIL import Image, ImageDraw, ImageFont
from pyzbar.pyzbar import decode as zbar_decode

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
OPTIONS_PATH = "/data/options.json"
CORE_API = "http://supervisor/core/api"

def load_options() -> dict:
    import json
    with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def pick_anchor(pos: str, w: int, h: int, text_w: int, text_h: int, pad: int) -> Tuple[int, int]:
    if pos == "top_left":
        return (pad, pad)
    if pos == "top_right":
        return (w - text_w - pad, pad)
    if pos == "bottom_right":
        return (w - text_w - pad, h - text_h - pad)
    return (pad, h - text_h - pad)

class State:
    def __init__(self):
        self.last_text = ""
        self.last_seen_ts = 0.0
        self.last_annotated_jpeg = b""
        self.last_raw_jpeg = b""
        self.source = ""

async def fetch_camera_snapshot_from_ha(session: ClientSession, entity_id: str) -> bytes:
    url = f"{CORE_API}/camera_proxy/{entity_id}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"camera_proxy failed: {resp.status} {body[:200]}")
        return await resp.read()

async def fetch_snapshot_from_rtsp(rtsp_url: str, transport: str = "tcp", timeout_s: int = 10) -> bytes:
    """Grab a single frame from RTSP and output it as JPEG to stdout using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", transport,
        "-i", rtsp_url,
        "-an",
        "-frames:v", "1",
        "-q:v", "3",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg timed out grabbing RTSP frame")

    if proc.returncode != 0 or not stdout:
        err = (stderr or b"").decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err}")

    return stdout

def decode_qr_text(jpeg_bytes: bytes) -> Optional[str]:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    results = zbar_decode(img)
    if not results:
        return None
    texts = []
    for r in results:
        texts.append(r.data.decode("utf-8", errors="replace"))
    return " | ".join(texts)

def annotate(jpeg_bytes: bytes, text: str, overlay_cfg: dict) -> bytes:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    font_size = int(overlay_cfg.get("font_size", 28))
    pad = int(overlay_cfg.get("padding", 10))
    pos = overlay_cfg.get("position", "bottom_left")

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    label = text if text else "—"
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    w, h = img.size
    x, y = pick_anchor(pos, w, h, text_w + 2 * pad, text_h + 2 * pad, pad)

    box = (x, y, x + text_w + 2 * pad, y + text_h + 2 * pad)
    draw.rectangle(box, fill=(0, 0, 0))
    draw.text((x + pad, y + pad), label, font=font, fill=(255, 255, 255))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()

async def updater(state: State, rtsp_url: str, rtsp_transport: str, entity_id: str, poll_ms: int, overlay_cfg: dict):
    timeout = ClientTimeout(total=10)
    async with ClientSession(timeout=timeout) as session:
        while True:
            try:
                if rtsp_url:
                    state.source = f"rtsp({rtsp_transport})"
                    raw = await fetch_snapshot_from_rtsp(rtsp_url, transport=rtsp_transport, timeout_s=10)
                else:
                    state.source = "homeassistant(camera_proxy)"
                    raw = await fetch_camera_snapshot_from_ha(session, entity_id)

                state.last_raw_jpeg = raw

                decoded = decode_qr_text(raw)
                if decoded:
                    state.last_text = decoded
                    state.last_seen_ts = time.time()

                state.last_annotated_jpeg = annotate(raw, state.last_text, overlay_cfg)
            except Exception as e:
                print(f"[updater] {e}", flush=True)

            await asyncio.sleep(poll_ms / 1000.0)

async def handle_snapshot(request):
    state: State = request.app["state"]
    if not state.last_annotated_jpeg:
        return web.Response(status=503, text="No frame yet")
    return web.Response(body=state.last_annotated_jpeg, content_type="image/jpeg")

async def handle_status(request):
    state: State = request.app["state"]
    return web.json_response({
        "source": state.source,
        "last_text": state.last_text,
        "last_seen_ts": state.last_seen_ts,
    })

async def handle_mjpeg(request):
    state: State = request.app["state"]
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-cache",
            "Connection": "close",
            "Pragma": "no-cache",
        },
    )
    await resp.prepare(request)
    try:
        while True:
            frame = state.last_annotated_jpeg
            if frame:
                await resp.write(b"--frame\r\n")
                await resp.write(b"Content-Type: image/jpeg\r\n")
                await resp.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                await resp.write(frame)
                await resp.write(b"\r\n")
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[mjpeg] {e}", flush=True)
    return resp

def main():
    if not SUPERVISOR_TOKEN:
        raise SystemExit("SUPERVISOR_TOKEN missing (ensure homeassistant_api: true)")

    opts = load_options()
    rtsp_url = (opts.get("rtsp_url") or "").strip()
    rtsp_transport = (opts.get("rtsp_transport") or "tcp").strip()
    entity_id = opts.get("camera_entity", "camera.front_door")
    poll_ms = int(opts.get("poll_interval_ms", 750))
    overlay_cfg = opts.get("overlay", {})

    state = State()

    app = web.Application()
    app["state"] = state
    app.add_routes([
        web.get("/snapshot.jpg", handle_snapshot),
        web.get("/mjpeg", handle_mjpeg),
        web.get("/status", handle_status),
    ])

    loop = asyncio.get_event_loop()
    loop.create_task(updater(state, rtsp_url, rtsp_transport, entity_id, poll_ms, overlay_cfg))

    web.run_app(app, host="0.0.0.0", port=8080)

if __name__ == "__main__":
    main()
