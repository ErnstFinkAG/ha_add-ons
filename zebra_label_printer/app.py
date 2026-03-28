import json
import logging
import math
import os
import socket
from io import BytesIO
import re
from typing import Dict, List, Tuple

from flask import Flask, Response, jsonify, render_template_string, request, send_file
import qrcode
from PIL import Image, ImageDraw, ImageFont


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
LOGGER = logging.getLogger("zebra_label_printer")

APP = Flask(__name__)
DOTS_PER_MM = 203 / 25.4  # 8 dots/mm for 203 dpi
PRINTER_MAX_WIDTH_DOTS = 1344  # Zebra ZT420/ZT421 203 dpi maximum print width
INGRESS_ALLOWED_IP = "172.30.32.2"
LOCAL_ALLOWED_IPS = {"127.0.0.1", "::1", None}
OPTIONS_PATH = "/data/options.json"
FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


DEFAULT_OPTIONS = {
    "printer_host": "192.168.1.50",
    "printer_port": 9100,
    "label_width_mm": 170.0,
    "label_height_mm": 305.0,
    "qr_size_mm": 150.0,
    "top_margin_mm": 5.0,
    "field1_label": "Text string 1",
    "field2_label": "Text string 2",
    "field3_label": "Text string 3",
    "qr_value_template": "text1",
    "qr_quiet_zone_modules": 4,
    "qr_error_correction": "M",
}

QR_ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

DEFAULT_FORM = {
    "text1": "250001 - Test Project",
    "text2": "Element 1e",
    "text3": "Zone A",
    "copies": "1",
}

ALLOWED_QR_TOKENS = ("text1", "text2", "text3")


def ingress_base_path() -> str:
    base = request.headers.get("X-Ingress-Path") or request.script_root or ""
    return base.rstrip("/")


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
      --label-bg: #ffffff;
      --label-ink: #111111;
      --label-edge: #d1d5db;
      --label-no-print: #f3f4f6;
    }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .wrap {
      max-width: 1100px;
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
    h1, h2, h3 {
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
    .preview-wrap {
      overflow: auto;
      background: #0b1220;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 16px;
    }
    .preview-stage {
      display: flex;
      justify-content: center;
      align-items: flex-start;
      min-width: min-content;
    }
    .preview-frame {
      width: min(100%, 420px);
      max-width: 100%;
      aspect-ratio: {{ requested_width_mm }} / {{ requested_height_mm }};
      background: var(--label-bg);
      border: 1px solid var(--label-edge);
      box-shadow: 0 10px 30px rgba(0,0,0,0.28);
    }
    .preview-frame img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: white;
    }
    .preview-meta {
      margin-top: 12px;
      font-size: 0.95rem;
      color: var(--muted);
    }
    .config-list {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
    }
    .config-list li + li {
      margin-top: 8px;
    }
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
      <form id="label-form" method="post" action="{{ ingress_base }}/print">
        <label for="text1">{{ field1_label }}</label>
        <textarea id="text1" name="text1" required>{{ form.text1 }}</textarea>

        <label for="text2">{{ field2_label }}</label>
        <input id="text2" name="text2" value="{{ form.text2 }}" required>

        <label for="text3">{{ field3_label }}</label>
        <input id="text3" name="text3" value="{{ form.text3 }}" required>

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
          <a id="preview-zpl-link" class="button-link secondary" href="{{ ingress_base }}/preview?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&copies={{ form.copies }}">Preview ZPL</a>
          <a id="preview-png-link" class="button-link secondary" href="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&copies={{ form.copies }}" target="_blank" rel="noopener">Open PNG preview</a>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Preview</h2>
      <div class="preview-wrap">
        <div class="preview-stage">
          <div class="preview-frame">
            <img id="preview-image" src="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&copies={{ form.copies }}" alt="Label preview">
          </div>
        </div>
      </div>
      <div class="preview-meta">
        PNG is rendered from the same layout coordinates as the generated ZPL and exported at 203 dpi.
        Screen size can still vary with browser zoom and display scaling.
      </div>
    </div>

    <div class="card">
      <h2>Configured label mapping</h2>
      <ul class="config-list">
        <li><strong>Field 1 label:</strong> <code>{{ field1_label }}</code></li>
        <li><strong>Field 2 label:</strong> <code>{{ field2_label }}</code></li>
        <li><strong>Field 3 label:</strong> <code>{{ field3_label }}</code></li>
        <li><strong>QR template:</strong> <code>{{ qr_value_template }}</code></li>
        <li><strong>QR quiet zone:</strong> <code>{{ qr_quiet_zone_modules }} module(s)</code></li>
        <li><strong>QR error correction:</strong> <code>{{ qr_error_correction }}</code></li>
        <li><strong>Current QR payload:</strong> <code>{{ qr_preview }}</code></li>
      </ul>
    </div>

    <div class="card">
      <h2>Layout</h2>
      <p class="muted">
        Requested label: {{ requested_width_mm }} × {{ requested_height_mm }} mm<br>
        Requested QR: {{ requested_qr_mm }} × {{ requested_qr_mm }} mm<br>
        QR quiet zone: {{ qr_quiet_zone_modules }} module(s), error correction: {{ qr_error_correction }}<br>
        Effective print width on ZT420/ZT421 @ 203 dpi: {{ effective_width_mm }} mm ({{ effective_width_dots }} dots)
      </p>
      {% if width_warning %}
      <p class="warn">{{ width_warning }}</p>
      {% endif %}
      <p class="muted">
        The QR code uses the configured template above. All three fields are printed below the QR code in human-readable form.
      </p>
    </div>
  </div>
  <script>
    (function () {
      const ingressBase = {{ ingress_base|tojson }};
      const text1 = document.getElementById("text1");
      const text2 = document.getElementById("text2");
      const text3 = document.getElementById("text3");
      const copies = document.getElementById("copies");
      const previewImage = document.getElementById("preview-image");
      const previewPngLink = document.getElementById("preview-png-link");
      const previewZplLink = document.getElementById("preview-zpl-link");

      if (!text1 || !text2 || !text3 || !copies || !previewImage || !previewPngLink || !previewZplLink) {
        return;
      }

      let refreshTimer = null;
      let previewNonce = Date.now();

      function normalizedCopies() {
        const raw = parseInt(copies.value || "1", 10);
        if (Number.isNaN(raw)) return "1";
        return String(Math.max(1, Math.min(50, raw)));
      }

      function buildQuery() {
        const params = new URLSearchParams();
        params.set("text1", text1.value || "");
        params.set("text2", text2.value || "");
        params.set("text3", text3.value || "");
        params.set("copies", normalizedCopies());
        return params;
      }

      function applyPreviewUpdate() {
        const params = buildQuery();
        previewNonce += 1;

        const pngParams = new URLSearchParams(params);
        pngParams.set("_", String(previewNonce));
        const pngUrl = `${ingressBase}/preview.png?${pngParams.toString()}`;
        previewImage.src = pngUrl;
        previewPngLink.href = `${ingressBase}/preview.png?${params.toString()}`;
        previewZplLink.href = `${ingressBase}/preview?${params.toString()}`;
      }

      function schedulePreviewUpdate() {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(applyPreviewUpdate, 180);
      }

      [text1, text2, text3, copies].forEach((input) => {
        input.addEventListener("input", schedulePreviewUpdate);
        input.addEventListener("change", applyPreviewUpdate);
      });
    })();
  </script>
</body>
</html>
"""


def load_options() -> Dict:
    options = dict(DEFAULT_OPTIONS)
    if os.path.exists(OPTIONS_PATH):
        try:
            with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                options.update(data)
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", OPTIONS_PATH, exc)

    options["field1_label"] = str(options.get("field1_label") or DEFAULT_OPTIONS["field1_label"]).strip() or DEFAULT_OPTIONS["field1_label"]
    options["field2_label"] = str(options.get("field2_label") or DEFAULT_OPTIONS["field2_label"]).strip() or DEFAULT_OPTIONS["field2_label"]
    options["field3_label"] = str(options.get("field3_label") or DEFAULT_OPTIONS["field3_label"]).strip() or DEFAULT_OPTIONS["field3_label"]
    options["qr_value_template"] = str(options.get("qr_value_template") or DEFAULT_OPTIONS["qr_value_template"])

    try:
        quiet_zone = int(options.get("qr_quiet_zone_modules", DEFAULT_OPTIONS["qr_quiet_zone_modules"]))
    except (TypeError, ValueError):
        quiet_zone = DEFAULT_OPTIONS["qr_quiet_zone_modules"]
    options["qr_quiet_zone_modules"] = max(0, min(20, quiet_zone))

    level = str(options.get("qr_error_correction") or DEFAULT_OPTIONS["qr_error_correction"]).strip().upper()
    options["qr_error_correction"] = level if level in QR_ERROR_CORRECTION_MAP else DEFAULT_OPTIONS["qr_error_correction"]
    return options


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


def qr_quiet_zone_modules(opts: Dict) -> int:
    try:
        quiet_zone = int(opts.get("qr_quiet_zone_modules", DEFAULT_OPTIONS["qr_quiet_zone_modules"]))
    except (TypeError, ValueError):
        quiet_zone = DEFAULT_OPTIONS["qr_quiet_zone_modules"]
    return max(0, min(20, quiet_zone))


def qr_error_correction_constant(opts: Dict) -> int:
    level = str(opts.get("qr_error_correction") or DEFAULT_OPTIONS["qr_error_correction"]).strip().upper()
    return QR_ERROR_CORRECTION_MAP.get(level, QR_ERROR_CORRECTION_MAP[DEFAULT_OPTIONS["qr_error_correction"]])


def build_qr_image(data: str, size_dots: int, opts: Dict) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qr_error_correction_constant(opts),
        box_size=10,
        border=qr_quiet_zone_modules(opts),
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


def build_qr_payload(text1: str, text2: str, text3: str, opts: Dict) -> str:
    template = str(opts.get("qr_value_template") or DEFAULT_OPTIONS["qr_value_template"])
    values = {
        "text1": text1,
        "text2": text2,
        "text3": text3,
    }

    invalid_tokens = sorted({token.lower() for token in re.findall(r"\btext\d+\b", template, flags=re.IGNORECASE)} - set(ALLOWED_QR_TOKENS))
    if invalid_tokens:
        allowed = ", ".join(ALLOWED_QR_TOKENS)
        raise ValueError(
            f"qr_value_template uses unsupported token(s): {', '.join(invalid_tokens)}. Allowed tokens: {allowed}."
        )

    def replace_token(match: re.Match[str]) -> str:
        return values[match.group(0).lower()]

    payload = re.sub(r"\b(?:text1|text2|text3)\b", replace_token, template, flags=re.IGNORECASE)
    payload = payload.strip()
    if not payload:
        raise ValueError("QR payload is empty. Adjust qr_value_template or enter field values.")
    return payload


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


def build_zpl(text1: str, text2: str, text3: str, copies: int, opts: Dict) -> str:
    layout = effective_layout(opts)
    qr_payload = build_qr_payload(text1, text2, text3, opts)

    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    qr_size = min(layout["qr_size_dots"], pw)
    qr_left = max((pw - qr_size) // 2, 0)
    qr_top = layout["top_margin_dots"]

    margin_x = max((pw - qr_size) // 2, mm_to_dots(8))
    text_width = pw - (margin_x * 2)
    primary_h, primary_w, primary_lines = font_for_text(text1, primary=True)
    secondary_h, secondary_w, secondary_lines = font_for_text(text2, primary=False)
    tertiary_h, tertiary_w, tertiary_lines = font_for_text(text3, primary=False)

    primary_y = qr_top + qr_size + mm_to_dots(8)
    secondary_y = primary_y + (primary_h * primary_lines) + mm_to_dots(8)
    tertiary_y = secondary_y + (secondary_h * secondary_lines) + mm_to_dots(6)

    qr_img = build_qr_image(qr_payload, qr_size, opts)
    total_bytes, bytes_per_row, graphic_hex = image_to_gfa(qr_img)

    esc1 = zpl_escape_utf8(text1)
    esc2 = zpl_escape_utf8(text2)
    esc3 = zpl_escape_utf8(text3)

    zpl = f"""^XA
^CI28
^PW{pw}
^LL{ll}
^LH0,0
^FO{qr_left},{qr_top}^GFA,{total_bytes},{total_bytes},{bytes_per_row},{graphic_hex}^FS
^FO{margin_x},{primary_y}^A0N,{primary_h},{primary_w}^FB{text_width},{primary_lines},14,C,0^FH\\^FD{esc1}^FS
^FO{margin_x},{secondary_y}^A0N,{secondary_h},{secondary_w}^FB{text_width},{secondary_lines},10,C,0^FH\\^FD{esc2}^FS
^FO{margin_x},{tertiary_y}^A0N,{tertiary_h},{tertiary_w}^FB{text_width},{tertiary_lines},10,C,0^FH\\^FD{esc3}^FS
^PQ{copies},0,1,N
^XZ"""
    return zpl


def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def text_line_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, bbox[3] - bbox[1])


def wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return [""]

    raw_parts = []
    for paragraph in text.splitlines() or [text]:
        paragraph = paragraph.strip()
        if not paragraph:
            raw_parts.append("")
            continue
        words = paragraph.split()
        if not words:
            raw_parts.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            bbox = draw.textbbox((0, 0), trial, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current = trial
            else:
                raw_parts.append(current)
                current = word
        raw_parts.append(current)

    if len(raw_parts) <= max_lines:
        return raw_parts

    trimmed = raw_parts[:max_lines]
    overflow = " ".join(raw_parts[max_lines - 1:]).strip()
    if not overflow:
        return trimmed

    ellipsis = "..."
    last = overflow
    while last:
        bbox = draw.textbbox((0, 0), last + ellipsis, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            trimmed[-1] = last + ellipsis
            return trimmed
        last = last[:-1].rstrip()
    trimmed[-1] = ellipsis
    return trimmed


def render_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    y: int,
    width: int,
    font: ImageFont.ImageFont,
    fill: int,
    line_spacing: int,
) -> None:
    x_center = width // 2
    line_h = text_line_height(draw, font)
    current_y = y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x_center - text_w / 2, current_y), line, font=font, fill=fill)
        current_y += line_h + line_spacing


def render_preview_image(text1: str, text2: str, text3: str, opts: Dict) -> Image.Image:
    layout = effective_layout(opts)
    qr_payload = build_qr_payload(text1, text2, text3, opts)

    requested_w = layout["requested_width_dots"]
    requested_h = layout["requested_height_dots"]
    pw = layout["effective_width_dots"]
    qr_size = min(layout["qr_size_dots"], pw)
    qr_left = max((pw - qr_size) // 2, 0)
    qr_top = layout["top_margin_dots"]

    img = Image.new("RGB", (requested_w, requested_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    if requested_w > pw:
        draw.rectangle((pw, 0, requested_w - 1, requested_h - 1), fill=(244, 244, 244))
        draw.line((pw, 0, pw, requested_h), fill=(180, 180, 180), width=1)

    draw.rectangle((0, 0, requested_w - 1, requested_h - 1), outline=(205, 205, 205), width=2)

    qr_img = build_qr_image(qr_payload, qr_size, opts).convert("RGB")
    img.paste(qr_img, (qr_left, qr_top))
    preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
    draw.rectangle(
        (qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1),
        outline=(220, 38, 38),
        width=preview_border_width,
    )

    margin_x = max((pw - qr_size) // 2, mm_to_dots(8))
    text_width = pw - (margin_x * 2)
    primary_h, _primary_w, primary_lines = font_for_text(text1, primary=True)
    secondary_h, _secondary_w, secondary_lines = font_for_text(text2, primary=False)
    tertiary_h, _tertiary_w, tertiary_lines = font_for_text(text3, primary=False)
    primary_y = qr_top + qr_size + mm_to_dots(8)
    secondary_y = primary_y + (primary_h * primary_lines) + mm_to_dots(8)
    tertiary_y = secondary_y + (secondary_h * secondary_lines) + mm_to_dots(6)

    primary_font = get_font(primary_h)
    secondary_font = get_font(secondary_h)
    tertiary_font = get_font(tertiary_h)
    primary_wrapped = wrap_text_lines(draw, text1, primary_font, text_width, primary_lines)
    secondary_wrapped = wrap_text_lines(draw, text2, secondary_font, text_width, secondary_lines)
    tertiary_wrapped = wrap_text_lines(draw, text3, tertiary_font, text_width, tertiary_lines)

    render_centered_lines(draw, primary_wrapped, primary_y, pw, primary_font, fill=(0, 0, 0), line_spacing=14)
    render_centered_lines(draw, secondary_wrapped, secondary_y, pw, secondary_font, fill=(0, 0, 0), line_spacing=10)
    render_centered_lines(draw, tertiary_wrapped, tertiary_y, pw, tertiary_font, fill=(0, 0, 0), line_spacing=10)

    return img


def send_to_printer(host: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    LOGGER.info("Sending %s bytes to printer %s:%s", len(data), host, port)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(data)
    LOGGER.info("Finished sending label payload to printer %s:%s", host, port)


def form_data_from_request() -> Dict[str, str]:
    return {
        "text1": request.values.get("text1", DEFAULT_FORM["text1"]),
        "text2": request.values.get("text2", DEFAULT_FORM["text2"]),
        "text3": request.values.get("text3", DEFAULT_FORM["text3"]),
        "copies": request.values.get("copies", DEFAULT_FORM["copies"]),
    }


def render_page(form: Dict[str, str], opts: Dict, result: Dict | None = None) -> str:
    layout = effective_layout(opts)
    try:
        qr_preview = build_qr_payload(form.get("text1", ""), form.get("text2", ""), form.get("text3", ""), opts)
    except Exception as exc:
        qr_preview = f"Configuration error: {exc}"

    return render_template_string(
        HTML,
        form=form,
        result=result,
        printer_host=opts["printer_host"],
        printer_port=opts["printer_port"],
        field1_label=opts["field1_label"],
        field2_label=opts["field2_label"],
        field3_label=opts["field3_label"],
        qr_value_template=opts["qr_value_template"],
        qr_quiet_zone_modules=opts["qr_quiet_zone_modules"],
        qr_error_correction=opts["qr_error_correction"],
        qr_preview=qr_preview,
        requested_width_mm=opts["label_width_mm"],
        requested_height_mm=opts["label_height_mm"],
        requested_qr_mm=opts["qr_size_mm"],
        effective_width_mm=dots_to_mm(layout["effective_width_dots"]),
        effective_width_dots=layout["effective_width_dots"],
        width_warning=layout["width_warning"],
        ingress_base=ingress_base_path(),
    )


@APP.before_request
def restrict_ingress():
    remote = request.remote_addr
    if request.headers.get("X-Ingress-Path"):
        return None
    if remote not in LOCAL_ALLOWED_IPS and remote != INGRESS_ALLOWED_IP:
        return Response("Forbidden", status=403)


@APP.route("/", methods=["GET"])
def index():
    opts = load_options()
    form = form_data_from_request()
    LOGGER.info("Opened UI for printer %s:%s", opts["printer_host"], opts["printer_port"])
    return render_page(form, opts, result=None)


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_options()
    text1 = request.form.get("text1", "").strip()
    text2 = request.form.get("text2", "").strip()
    text3 = request.form.get("text3", "").strip()
    copies_raw = request.form.get("copies", DEFAULT_FORM["copies"]).strip()

    result = {"success": False, "message": "Unknown error"}
    try:
        if not text1:
            raise ValueError(f"{opts['field1_label']} is required.")
        if not text2:
            raise ValueError(f"{opts['field2_label']} is required.")
        if not text3:
            raise ValueError(f"{opts['field3_label']} is required.")
        copies = max(1, min(50, int(copies_raw)))
        zpl = build_zpl(text1, text2, text3, copies, opts)
        qr_payload = build_qr_payload(text1, text2, text3, opts)
        LOGGER.info("Print request received: copies=%s qr_payload=%r", copies, qr_payload)
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        result = {
            "success": True,
            "message": f"Sent {copies} label(s) to {opts['printer_host']}:{opts['printer_port']}. QR payload: {qr_payload}",
        }
    except Exception as exc:
        LOGGER.exception("Print failed")
        result = {"success": False, "message": f"Print failed: {exc}"}

    form = {"text1": text1, "text2": text2, "text3": text3, "copies": copies_raw or DEFAULT_FORM["copies"]}
    return render_page(form, opts, result=result)


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_options()
    text1 = request.args.get("text1", DEFAULT_FORM["text1"])
    text2 = request.args.get("text2", DEFAULT_FORM["text2"])
    text3 = request.args.get("text3", DEFAULT_FORM["text3"])
    copies = max(1, min(50, int(request.args.get("copies", DEFAULT_FORM["copies"]))))
    zpl = build_zpl(text1, text2, text3, copies, opts)
    LOGGER.info("Generated ZPL preview for copies=%s", copies)
    return Response(zpl, mimetype="text/plain; charset=utf-8")


@APP.route("/preview.png", methods=["GET"])
def preview_png():
    opts = load_options()
    text1 = request.args.get("text1", DEFAULT_FORM["text1"])
    text2 = request.args.get("text2", DEFAULT_FORM["text2"])
    text3 = request.args.get("text3", DEFAULT_FORM["text3"])
    LOGGER.info("Generating PNG preview for payload inputs text1=%r text2=%r text3=%r", text1, text2, text3)
    img = render_preview_image(text1, text2, text3, opts)
    bio = BytesIO()
    img.save(bio, format="PNG", dpi=(203, 203), optimize=True)
    bio.seek(0)
    return send_file(bio, mimetype="image/png", download_name="label-preview.png")


@APP.route("/api/print", methods=["POST"])
def api_print():
    opts = load_options()
    payload = request.get_json(force=True, silent=False) or {}
    text1 = str(payload.get("text1", "")).strip()
    text2 = str(payload.get("text2", "")).strip()
    text3 = str(payload.get("text3", "")).strip()
    copies = max(1, min(50, int(payload.get("copies", 1))))
    if not text1 or not text2 or not text3:
        return jsonify({
            "ok": False,
            "error": f"{opts['field1_label']}, {opts['field2_label']}, and {opts['field3_label']} are required.",
        }), 400
    try:
        zpl = build_zpl(text1, text2, text3, copies, opts)
        LOGGER.info("API print request received: copies=%s", copies)
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        return jsonify({
            "ok": True,
            "printer": opts["printer_host"],
            "copies": copies,
            "qr_payload": build_qr_payload(text1, text2, text3, opts),
        })
    except Exception as exc:
        LOGGER.exception("API print failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8099, debug=False)
