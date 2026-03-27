import json
import math
import os
import socket
from io import BytesIO
from typing import Dict, Tuple

from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
import qrcode
from PIL import Image


APP = Flask(__name__)
DOTS_PER_MM = 203 / 25.4  # 8 dots/mm for 203 dpi
PRINTER_MAX_WIDTH_DOTS = 1344  # Zebra ZT420/ZT421 203 dpi maximum print width
INGRESS_ALLOWED_IP = "172.30.32.2"
LOCAL_ALLOWED_IPS = {"127.0.0.1", "::1", None}
OPTIONS_PATH = "/data/options.json"

HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zebra Label Printer</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #111827;
      --card: #1f2937;
      --text: #f9fafb;
      --muted: #cbd5e1;
      --accent: #3b82f6;
      --danger: #ef4444;
      --ok: #10b981;
      --border: #374151;
    }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .wrap {
      max-width: 900px;
      margin: 0 auto;
      padding: 24px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.25);
      margin-bottom: 20px;
    }
    h1, h2 {
      margin-top: 0;
    }
    label {
      display: block;
      font-weight: 600;
      margin-bottom: 8px;
    }
    input, textarea {
      width: 100%;
      box-sizing: border-box;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      padding: 12px 14px;
      font: inherit;
      margin-bottom: 16px;
    }
    textarea { min-height: 84px; }
    .row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }
    .btns {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    button, .button-link {
      border: none;
      background: var(--accent);
      color: white;
      padding: 12px 18px;
      border-radius: 12px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
    }
    .secondary {
      background: transparent;
      border: 1px solid var(--border);
    }
    .flash {
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
    }
    .flash.ok { background: rgba(16,185,129,0.14); border: 1px solid var(--ok); }
    .flash.error { background: rgba(239,68,68,0.14); border: 1px solid var(--danger); }
    code, pre {
      background: #0b1220;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .muted { color: var(--muted); }
    .warn { color: #fbbf24; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Zebra Label Printer</h1>
      <p class="muted">
        Prints one large QR code label to a networked Zebra printer using raw ZPL over TCP.
      </p>
      {% if result %}
        <div class="flash {{ 'ok' if result.success else 'error' }}">{{ result.message }}</div>
      {% endif %}
      <form method="post" action="{{ url_for('print_label') }}">
        <label for="text1">Text string 1</label>
        <textarea id="text1" name="text1" required>{{ form.text1 }}</textarea>

        <label for="text2">Text string 2</label>
        <input id="text2" name="text2" value="{{ form.text2 }}" required>

        <div class="row">
          <div>
            <label for="copies">Copies</label>
            <input id="copies" name="copies" type="number" min="1" max="50" value="{{ form.copies }}" required>
          </div>
          <div>
            <label>Configured printer</label>
            <input value="{{ printer_host }}:{{ printer_port }}" disabled>
          </div>
        </div>

        <div class="btns">
          <button type="submit">Print label</button>
          <a class="button-link secondary" href="{{ url_for('preview') }}?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&copies={{ form.copies }}">Preview ZPL</a>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Layout</h2>
      <p class="muted">
        Requested label: {{ requested_width_mm }} × {{ requested_height_mm }} mm<br>
        Requested QR: {{ requested_qr_mm }} × {{ requested_qr_mm }} mm<br>
        Effective print width on ZT420/ZT421 @ 203 dpi: {{ effective_width_mm }} mm ({{ effective_width_dots }} dots)
      </p>
      {% if width_warning %}
      <p class="warn">{{ width_warning }}</p>
      {% endif %}
      <p class="muted">
        The QR code encodes Text string 1. Both strings are printed below the QR code.
      </p>
    </div>
  </div>
</body>
</html>
"""


def load_options() -> Dict:
    defaults = {
        "printer_host": "192.168.1.50",
        "printer_port": 9100,
        "label_width_mm": 170.0,
        "label_height_mm": 305.0,
        "qr_size_mm": 150.0,
        "top_margin_mm": 5.0,
    }
    if os.path.exists(OPTIONS_PATH):
        try:
            with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            defaults.update(data)
        except Exception:
            pass
    return defaults


def mm_to_dots(mm_value: float) -> int:
    return max(1, int(round(float(mm_value) * DOTS_PER_MM)))


def dots_to_mm(dots: int) -> float:
    return round(dots / DOTS_PER_MM, 1)


def effective_layout(opts: Dict) -> Dict:
    requested_width_dots = mm_to_dots(opts["label_width_mm"])
    requested_height_dots = mm_to_dots(opts["label_height_mm"])
    qr_size_dots = mm_to_dots(opts["qr_size_mm"])
    top_margin_dots = mm_to_dots(opts["top_margin_mm"])
    effective_width_dots = min(requested_width_dots, PRINTER_MAX_WIDTH_DOTS)
    width_warning = None
    if requested_width_dots > PRINTER_MAX_WIDTH_DOTS:
        width_warning = (
            f"Requested width {opts['label_width_mm']} mm exceeds the printer's 168 mm printable width. "
            f"The add-on will clamp ZPL ^PW to {dots_to_mm(effective_width_dots)} mm."
        )
    return {
        "requested_width_dots": requested_width_dots,
        "requested_height_dots": requested_height_dots,
        "qr_size_dots": qr_size_dots,
        "top_margin_dots": top_margin_dots,
        "effective_width_dots": effective_width_dots,
        "width_warning": width_warning,
    }


def build_qr_image(data: str, size_dots: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")
    return img.resize((size_dots, size_dots), Image.Resampling.NEAREST)


def image_to_gfa(img: Image.Image) -> Tuple[int, int, str]:
    mono = img.convert("1")
    width, height = mono.size
    bytes_per_row = math.ceil(width / 8)
    total_bytes = bytes_per_row * height
    pixels = mono.load()
    lines = []
    for y in range(height):
        row = bytearray()
        for xb in range(bytes_per_row):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < width:
                    pixel = pixels[x, y]
                    is_black = pixel == 0
                    if is_black:
                        byte |= 1 << (7 - bit)
            row.append(byte)
        lines.append(row.hex().upper())
    return total_bytes, bytes_per_row, "".join(lines)


def zpl_escape_utf8(text: str) -> str:
    safe = set(range(32, 127)) - {ord("^"), ord("~"), ord("\\")}
    out = []
    for b in text.encode("utf-8"):
        if b in safe:
            out.append(chr(b))
        else:
            out.append(f"\\{b:02X}")
    return "".join(out)


def font_for_text(text: str, primary: bool) -> Tuple[int, int, int]:
    length = len(text.strip())
    if primary:
        if length <= 24:
            return 110, 88, 2
        if length <= 36:
            return 90, 72, 3
        return 72, 58, 4
    if length <= 20:
        return 95, 76, 2
    if length <= 30:
        return 80, 64, 2
    return 68, 54, 3


def build_zpl(text1: str, text2: str, copies: int, opts: Dict) -> str:
    layout = effective_layout(opts)
    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    qr_size = min(layout["qr_size_dots"], pw)
    qr_left = max((pw - qr_size) // 2, 0)
    qr_top = layout["top_margin_dots"]

    margin_x = max((pw - qr_size) // 2, mm_to_dots(8))
    text_width = pw - (margin_x * 2)
    primary_h, primary_w, primary_lines = font_for_text(text1, primary=True)
    secondary_h, secondary_w, secondary_lines = font_for_text(text2, primary=False)

    primary_y = qr_top + qr_size + mm_to_dots(8)
    secondary_y = primary_y + (primary_h * primary_lines) + mm_to_dots(8)

    qr_img = build_qr_image(text1, qr_size)
    total_bytes, bytes_per_row, graphic_hex = image_to_gfa(qr_img)

    esc1 = zpl_escape_utf8(text1)
    esc2 = zpl_escape_utf8(text2)

    zpl = f"""^XA
^CI28
^PW{pw}
^LL{ll}
^LH0,0
^FO{qr_left},{qr_top}^GFA,{total_bytes},{total_bytes},{bytes_per_row},{graphic_hex}^FS
^FO{margin_x},{primary_y}^A0N,{primary_h},{primary_w}^FB{text_width},{primary_lines},14,C,0^FH\\^FD{esc1}^FS
^FO{margin_x},{secondary_y}^A0N,{secondary_h},{secondary_w}^FB{text_width},{secondary_lines},10,C,0^FH\\^FD{esc2}^FS
^PQ{copies},0,1,N
^XZ"""
    return zpl


def send_to_printer(host: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(data)


@APP.before_request
def restrict_ingress():
    remote = request.remote_addr
    if remote not in LOCAL_ALLOWED_IPS and remote != INGRESS_ALLOWED_IP:
        return Response("Forbidden", status=403)


@APP.route("/", methods=["GET"])
def index():
    opts = load_options()
    layout = effective_layout(opts)
    form = {
        "text1": request.args.get("text1", "250001 - Test Project"),
        "text2": request.args.get("text2", "Element 1e"),
        "copies": request.args.get("copies", "1"),
    }
    return render_template_string(
        HTML,
        form=form,
        result=None,
        printer_host=opts["printer_host"],
        printer_port=opts["printer_port"],
        requested_width_mm=opts["label_width_mm"],
        requested_height_mm=opts["label_height_mm"],
        requested_qr_mm=opts["qr_size_mm"],
        effective_width_mm=dots_to_mm(layout["effective_width_dots"]),
        effective_width_dots=layout["effective_width_dots"],
        width_warning=layout["width_warning"],
    )


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_options()
    text1 = request.form.get("text1", "").strip()
    text2 = request.form.get("text2", "").strip()
    copies_raw = request.form.get("copies", "1").strip()

    result = {"success": False, "message": "Unknown error"}
    try:
        if not text1:
            raise ValueError("Text string 1 is required.")
        if not text2:
            raise ValueError("Text string 2 is required.")
        copies = max(1, min(50, int(copies_raw)))
        zpl = build_zpl(text1, text2, copies, opts)
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        result = {
            "success": True,
            "message": f"Sent {copies} label(s) to {opts['printer_host']}:{opts['printer_port']}.",
        }
    except Exception as exc:
        result = {"success": False, "message": f"Print failed: {exc}"}

    layout = effective_layout(opts)
    form = {"text1": text1, "text2": text2, "copies": copies_raw or "1"}
    return render_template_string(
        HTML,
        form=form,
        result=result,
        printer_host=opts["printer_host"],
        printer_port=opts["printer_port"],
        requested_width_mm=opts["label_width_mm"],
        requested_height_mm=opts["label_height_mm"],
        requested_qr_mm=opts["qr_size_mm"],
        effective_width_mm=dots_to_mm(layout["effective_width_dots"]),
        effective_width_dots=layout["effective_width_dots"],
        width_warning=layout["width_warning"],
    )


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_options()
    text1 = request.args.get("text1", "250001 - Test Project")
    text2 = request.args.get("text2", "Element 1e")
    copies = max(1, min(50, int(request.args.get("copies", "1"))))
    zpl = build_zpl(text1, text2, copies, opts)
    return Response(zpl, mimetype="text/plain; charset=utf-8")


@APP.route("/api/print", methods=["POST"])
def api_print():
    opts = load_options()
    payload = request.get_json(force=True, silent=False) or {}
    text1 = str(payload.get("text1", "")).strip()
    text2 = str(payload.get("text2", "")).strip()
    copies = max(1, min(50, int(payload.get("copies", 1))))
    if not text1 or not text2:
        return jsonify({"ok": False, "error": "text1 and text2 are required"}), 400
    try:
        zpl = build_zpl(text1, text2, copies, opts)
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        return jsonify({"ok": True, "printer": opts["printer_host"], "copies": copies})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8099, debug=False)
