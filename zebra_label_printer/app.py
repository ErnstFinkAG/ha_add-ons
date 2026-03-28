import json
import logging
import math
import os
import re
import socket
from functools import lru_cache
from io import BytesIO
from typing import Dict, List, Tuple

from flask import Flask, Response, jsonify, render_template_string, request, send_file
import qrcode
from PIL import Image, ImageDraw, ImageFont


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
root_logger = logging.getLogger()
if not root_logger.handlers:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
else:
    for handler in root_logger.handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.setLevel(logging.INFO)

logging.getLogger("werkzeug").handlers.clear()
logging.getLogger("werkzeug").propagate = True

LOGGER = logging.getLogger("zebra_label_printer")
APP = Flask(__name__)
APP.logger.handlers.clear()
APP.logger.propagate = True

DOTS_PER_MM = 203 / 25.4  # 8 dots/mm for 203 dpi
PRINTER_MAX_WIDTH_DOTS = 1344  # Zebra ZT420/ZT421 203 dpi maximum print width
INGRESS_ALLOWED_IP = "172.30.32.2"
LOCAL_ALLOWED_IPS = {"127.0.0.1", "::1", None}
OPTIONS_PATH = "/data/options.json"
DEFAULT_TEXT_BLOCK_MARGIN_MM = 8.0
FIELD_COUNT = 3
ALLOWED_QR_TOKENS = ("text1", "text2", "text3")
ALIGNMENTS = {"left", "center", "right"}
FONT_FAMILIES = {"sans", "serif", "mono"}
FIELD_GAPS_MM = {1: 8.0, 2: 6.0, 3: 4.0}
FIELD_MAX_LINES = {1: 4, 2: 3, 3: 3}
FOOTER_MAX_LINES = 3

FONT_PATHS = {
    "sans": {
        "regular": [
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ],
        "bold": [
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf",
        ],
        "bolditalic": [
            "/usr/share/fonts/TTF/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-BoldOblique.ttf",
        ],
    },
    "serif": {
        "regular": [
            "/usr/share/fonts/TTF/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        ],
        "bold": [
            "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/TTF/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/dejavu/DejaVuSerif-Italic.ttf",
        ],
        "bolditalic": [
            "/usr/share/fonts/TTF/DejaVuSerif-BoldItalic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
            "/usr/share/fonts/dejavu/DejaVuSerif-BoldItalic.ttf",
        ],
    },
    "mono": {
        "regular": [
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        ],
        "bold": [
            "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/TTF/DejaVuSansMono-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono-Oblique.ttf",
        ],
        "bolditalic": [
            "/usr/share/fonts/TTF/DejaVuSansMono-BoldOblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono-BoldOblique.ttf",
        ],
    },
}

DEFAULT_OPTIONS = {
    "printer_host": "10.50.20.12",
    "printer_port": 9100,
    "label_width_mm": 170.0,
    "label_height_mm": 305.0,
    "qr_size_mm": 170.0,
    "top_margin_mm": 0.0,
    "field1_label": "Projektnummer",
    "field2_label": "Projektname",
    "field3_label": "Element",
    "field1_default_value": "250001",
    "field2_default_value": "EFH Huggentobbler Biel",
    "field3_default_value": "DE1",
    "field1_alignment": "center",
    "field2_alignment": "center",
    "field3_alignment": "center",
    "field1_font_family": "sans",
    "field2_font_family": "sans",
    "field3_font_family": "sans",
    "field1_font_size_mm": 18.0,
    "field2_font_size_mm": 13.0,
    "field3_font_size_mm": 9.0,
    "field1_bold": True,
    "field2_bold": False,
    "field3_bold": False,
    "field1_italic": False,
    "field2_italic": False,
    "field3_italic": False,
    "field1_underline": False,
    "field2_underline": False,
    "field3_underline": False,
    "footer_label": "Footer",
    "footer_default_value": "Ernst Fink AG, Schorenweg 144, 4585 Biezwil",
    "footer_alignment": "center",
    "footer_font_family": "sans",
    "footer_font_size_mm": 7.0,
    "footer_bold": False,
    "footer_italic": False,
    "footer_underline": False,
    "qr_value_template": "text1 - text2",
    "qr_quiet_zone_modules": 3,
    "qr_error_correction": "M",
}

QR_ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

DEFAULT_FORM = {
    "copies": "1",
}

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
      --label-edge: #d1d5db;
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
    h1, h2, h3 { margin-top: 0; }
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
    .config-list li + li { margin-top: 8px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Zebra Label Printer</h1>
      <p class="muted">Prints one large QR code label to a networked Zebra printer using raw ZPL over TCP.</p>
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

        <label for="footer">{{ footer_label }}</label>
        <input id="footer" name="footer" value="{{ form.footer }}">

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
          <a id="preview-zpl-link" class="button-link secondary" href="{{ ingress_base }}/preview?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}">Preview ZPL</a>
          <a id="preview-png-link" class="button-link secondary" href="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}" target="_blank" rel="noopener">Open PNG preview</a>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Preview</h2>
      <div class="preview-wrap">
        <div class="preview-stage">
          <div class="preview-frame">
            <img id="preview-image" src="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}" alt="Label preview">
          </div>
        </div>
      </div>
      <div class="preview-meta">
        PNG is rendered from the same layout coordinates used for print generation and exported at 203 dpi.
        Screen size can still vary with browser zoom and display scaling.
      </div>
    </div>

    <div class="card">
      <h2>Configured label mapping</h2>
      <ul class="config-list">
        <li><strong>Field 1 label:</strong> <code>{{ field1_label }}</code> · <strong>default:</strong> <code>{{ field1_default_value }}</code> · <strong>style:</strong> <code>{{ field1_style_summary }}</code></li>
        <li><strong>Field 2 label:</strong> <code>{{ field2_label }}</code> · <strong>default:</strong> <code>{{ field2_default_value }}</code> · <strong>style:</strong> <code>{{ field2_style_summary }}</code></li>
        <li><strong>Field 3 label:</strong> <code>{{ field3_label }}</code> · <strong>default:</strong> <code>{{ field3_default_value }}</code> · <strong>style:</strong> <code>{{ field3_style_summary }}</code></li>
        <li><strong>Footer label:</strong> <code>{{ footer_label }}</code> · <strong>default:</strong> <code>{{ footer_default_value }}</code> · <strong>style:</strong> <code>{{ footer_style_summary }}</code></li>
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
      <p class="muted">All configured text fields are printed in human-readable form, including the footer. The QR code follows the configured template above.</p>
    </div>
  </div>
  <script>
    (function () {
      const ingressBase = {{ ingress_base|tojson }};
      const text1 = document.getElementById("text1");
      const text2 = document.getElementById("text2");
      const text3 = document.getElementById("text3");
      const footer = document.getElementById("footer");
      const copies = document.getElementById("copies");
      const previewImage = document.getElementById("preview-image");
      const previewPngLink = document.getElementById("preview-png-link");
      const previewZplLink = document.getElementById("preview-zpl-link");
      if (!text1 || !text2 || !text3 || !footer || !copies || !previewImage || !previewPngLink || !previewZplLink) return;

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
        params.set("footer", footer.value || "");
        params.set("copies", normalizedCopies());
        return params;
      }

      function applyPreviewUpdate() {
        const params = buildQuery();
        previewNonce += 1;
        const pngParams = new URLSearchParams(params);
        pngParams.set("_", String(previewNonce));
        previewImage.src = `${ingressBase}/preview.png?${pngParams.toString()}`;
        previewPngLink.href = `${ingressBase}/preview.png?${params.toString()}`;
        previewZplLink.href = `${ingressBase}/preview?${params.toString()}`;
      }

      function schedulePreviewUpdate() {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(applyPreviewUpdate, 180);
      }

      [text1, text2, text3, footer, copies].forEach((input) => {
        input.addEventListener("input", schedulePreviewUpdate);
        input.addEventListener("change", applyPreviewUpdate);
      });
    })();
  </script>
</body>
</html>
"""


def ingress_base_path() -> str:
    base = request.headers.get("X-Ingress-Path") or request.script_root or ""
    return base.rstrip("/")


@lru_cache(maxsize=128)
def load_font(family: str, bold: bool, italic: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    family = family if family in FONT_FAMILIES else "sans"
    size = max(10, int(size))
    style = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    fallback_order = [style, "bold" if bold else "regular", "italic" if italic else "regular", "regular"]
    tried = set()
    for style_name in fallback_order:
        if style_name in tried:
            continue
        tried.add(style_name)
        for path in FONT_PATHS[family][style_name]:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def normalize_string(value: object, default: str) -> str:
    text = str(value if value is not None else default)
    return text.strip() or default


def normalize_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def normalize_alignment(value: object, default: str) -> str:
    align = str(value if value is not None else default).strip().lower()
    return align if align in ALIGNMENTS else default


def normalize_font_family(value: object, default: str) -> str:
    family = str(value if value is not None else default).strip().lower()
    return family if family in FONT_FAMILIES else default


def load_options() -> Dict:
    options = dict(DEFAULT_OPTIONS)
    if os.path.exists(OPTIONS_PATH):
        try:
            with open(OPTIONS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                options.update(data)
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", OPTIONS_PATH, exc)

    options["printer_host"] = normalize_string(options.get("printer_host"), DEFAULT_OPTIONS["printer_host"])
    options["printer_port"] = normalize_int(options.get("printer_port"), DEFAULT_OPTIONS["printer_port"], 1, 65535)
    options["label_width_mm"] = normalize_float(options.get("label_width_mm"), DEFAULT_OPTIONS["label_width_mm"], 50.0, 500.0)
    options["label_height_mm"] = normalize_float(options.get("label_height_mm"), DEFAULT_OPTIONS["label_height_mm"], 50.0, 1000.0)
    options["qr_size_mm"] = normalize_float(options.get("qr_size_mm"), DEFAULT_OPTIONS["qr_size_mm"], 10.0, 300.0)
    options["top_margin_mm"] = normalize_float(options.get("top_margin_mm"), DEFAULT_OPTIONS["top_margin_mm"], 0.0, 100.0)
    options["qr_value_template"] = str(options.get("qr_value_template") or DEFAULT_OPTIONS["qr_value_template"])
    options["qr_quiet_zone_modules"] = normalize_int(options.get("qr_quiet_zone_modules"), DEFAULT_OPTIONS["qr_quiet_zone_modules"], 0, 20)
    level = str(options.get("qr_error_correction") or DEFAULT_OPTIONS["qr_error_correction"]).strip().upper()
    options["qr_error_correction"] = level if level in QR_ERROR_CORRECTION_MAP else DEFAULT_OPTIONS["qr_error_correction"]

    for idx in range(1, FIELD_COUNT + 1):
        options[f"field{idx}_label"] = normalize_string(options.get(f"field{idx}_label"), DEFAULT_OPTIONS[f"field{idx}_label"])
        options[f"field{idx}_default_value"] = str(options.get(f"field{idx}_default_value") or DEFAULT_OPTIONS[f"field{idx}_default_value"])
        options[f"field{idx}_alignment"] = normalize_alignment(options.get(f"field{idx}_alignment"), DEFAULT_OPTIONS[f"field{idx}_alignment"])
        options[f"field{idx}_font_family"] = normalize_font_family(options.get(f"field{idx}_font_family"), DEFAULT_OPTIONS[f"field{idx}_font_family"])
        options[f"field{idx}_font_size_mm"] = normalize_float(options.get(f"field{idx}_font_size_mm"), DEFAULT_OPTIONS[f"field{idx}_font_size_mm"], 2.0, 30.0)
        options[f"field{idx}_bold"] = normalize_bool(options.get(f"field{idx}_bold"), DEFAULT_OPTIONS[f"field{idx}_bold"])
        options[f"field{idx}_italic"] = normalize_bool(options.get(f"field{idx}_italic"), DEFAULT_OPTIONS[f"field{idx}_italic"])
        options[f"field{idx}_underline"] = normalize_bool(options.get(f"field{idx}_underline"), DEFAULT_OPTIONS[f"field{idx}_underline"])
    options["footer_label"] = normalize_string(options.get("footer_label"), DEFAULT_OPTIONS["footer_label"])
    options["footer_default_value"] = str(options.get("footer_default_value") or DEFAULT_OPTIONS["footer_default_value"])
    options["footer_alignment"] = normalize_alignment(options.get("footer_alignment"), DEFAULT_OPTIONS["footer_alignment"])
    options["footer_font_family"] = normalize_font_family(options.get("footer_font_family"), DEFAULT_OPTIONS["footer_font_family"])
    options["footer_font_size_mm"] = normalize_float(options.get("footer_font_size_mm"), DEFAULT_OPTIONS["footer_font_size_mm"], 2.0, 30.0)
    options["footer_bold"] = normalize_bool(options.get("footer_bold"), DEFAULT_OPTIONS["footer_bold"])
    options["footer_italic"] = normalize_bool(options.get("footer_italic"), DEFAULT_OPTIONS["footer_italic"])
    options["footer_underline"] = normalize_bool(options.get("footer_underline"), DEFAULT_OPTIONS["footer_underline"])
    return options


def default_form_from_options(opts: Dict) -> Dict[str, str]:
    return {
        "text1": str(opts.get("field1_default_value") or ""),
        "text2": str(opts.get("field2_default_value") or ""),
        "text3": str(opts.get("field3_default_value") or ""),
        "footer": str(opts.get("footer_default_value") or ""),
        "copies": DEFAULT_FORM["copies"],
    }


def form_data_from_request(opts: Dict) -> Dict[str, str]:
    defaults = default_form_from_options(opts)
    return {
        "text1": request.values.get("text1", defaults["text1"]),
        "text2": request.values.get("text2", defaults["text2"]),
        "text3": request.values.get("text3", defaults["text3"]),
        "footer": request.values.get("footer", defaults["footer"]),
        "copies": request.values.get("copies", defaults["copies"]),
    }


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
            f"The add-on will clamp the printed width to {dots_to_mm(effective_width_dots)} mm."
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
    return normalize_int(opts.get("qr_quiet_zone_modules"), DEFAULT_OPTIONS["qr_quiet_zone_modules"], 0, 20)


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
                if x < width and pixels[x, y] == 0:
                    byte |= 1 << (7 - bit)
            row.append(byte)
        lines.append(row.hex().upper())
    return total_bytes, bytes_per_row, "".join(lines)


def build_qr_payload(text1: str, text2: str, text3: str, opts: Dict) -> str:
    template = str(opts.get("qr_value_template") or DEFAULT_OPTIONS["qr_value_template"]).strip()
    if template.startswith("{") and template.endswith("}") and len(template) >= 2:
        template = template[1:-1].strip()
    values = {"text1": text1, "text2": text2, "text3": text3}
    invalid_tokens = sorted({token.lower() for token in re.findall(r"\btext\d+\b", template, flags=re.IGNORECASE)} - set(ALLOWED_QR_TOKENS))
    if invalid_tokens:
        allowed = ", ".join(ALLOWED_QR_TOKENS)
        raise ValueError(f"qr_value_template uses unsupported token(s): {', '.join(invalid_tokens)}. Allowed tokens: {allowed}.")

    def replace_token(match: re.Match[str]) -> str:
        return values[match.group(0).lower()]

    payload = re.sub(r"\b(?:text1|text2|text3)\b", replace_token, template, flags=re.IGNORECASE).strip()
    if not payload:
        raise ValueError("QR payload is empty. Adjust qr_value_template or enter field values.")
    return payload


def style_summary_from_prefix(opts: Dict, prefix: str) -> str:
    parts = [
        f"{opts[f'{prefix}_alignment']}",
        f"{opts[f'{prefix}_font_family']}",
        f"{opts[f'{prefix}_font_size_mm']} mm",
    ]
    if opts.get(f"{prefix}_bold"):
        parts.append("bold")
    if opts.get(f"{prefix}_italic"):
        parts.append("italic")
    if opts.get(f"{prefix}_underline"):
        parts.append("underline")
    return ", ".join(parts)


def field_style_summary(opts: Dict, idx: int) -> str:
    return style_summary_from_prefix(opts, f"field{idx}")


def footer_style_summary(opts: Dict) -> str:
    return style_summary_from_prefix(opts, "footer")


def get_field_config(opts: Dict, idx: int) -> Dict:
    return {
        "label": opts[f"field{idx}_label"],
        "default_value": opts[f"field{idx}_default_value"],
        "alignment": opts[f"field{idx}_alignment"],
        "font_family": opts[f"field{idx}_font_family"],
        "font_size_mm": opts[f"field{idx}_font_size_mm"],
        "bold": opts[f"field{idx}_bold"],
        "italic": opts[f"field{idx}_italic"],
        "underline": opts[f"field{idx}_underline"],
        "max_lines": FIELD_MAX_LINES[idx],
        "gap_after_dots": mm_to_dots(FIELD_GAPS_MM[idx]),
    }


def get_footer_config(opts: Dict) -> Dict:
    return {
        "label": opts["footer_label"],
        "default_value": opts["footer_default_value"],
        "alignment": opts["footer_alignment"],
        "font_family": opts["footer_font_family"],
        "font_size_mm": opts["footer_font_size_mm"],
        "bold": opts["footer_bold"],
        "italic": opts["footer_italic"],
        "underline": opts["footer_underline"],
        "max_lines": FOOTER_MAX_LINES,
        "gap_after_dots": 0,
    }


def text_line_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, bbox[3] - bbox[1])


def wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return [""]

    raw_parts: List[str] = []
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


def fit_field_lines(draw: ImageDraw.ImageDraw, text: str, cfg: Dict, max_width: int) -> Tuple[ImageFont.ImageFont, List[str], int]:
    start_size = max(10, mm_to_dots(cfg["font_size_mm"]))
    min_size = max(10, int(start_size * 0.6))
    best_font = load_font(cfg["font_family"], cfg["bold"], cfg["italic"], start_size)
    best_lines = wrap_text_lines(draw, text, best_font, max_width, cfg["max_lines"])
    best_size = start_size

    for size in range(start_size, min_size - 1, -1):
        font = load_font(cfg["font_family"], cfg["bold"], cfg["italic"], size)
        lines = wrap_text_lines(draw, text, font, max_width, cfg["max_lines"])
        if len(lines) < len(best_lines):
            best_font, best_lines, best_size = font, lines, size
            continue
        if len(lines) == len(best_lines):
            best_font, best_lines, best_size = font, lines, size
            widths = []
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                widths.append(bbox[2] - bbox[0])
            if widths and max(widths) <= max_width:
                break
    return best_font, best_lines, best_size


def draw_aligned_text_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    y: int,
    box_left: int,
    box_width: int,
    font: ImageFont.ImageFont,
    alignment: str,
    underline: bool,
    fill: Tuple[int, int, int],
    line_spacing: int,
) -> int:
    current_y = y
    line_h = text_line_height(draw, font)
    underline_thickness = max(1, line_h // 18)
    underline_offset = max(2, line_h // 12)

    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        if alignment == "left":
            x = box_left
        elif alignment == "right":
            x = box_left + box_width - text_w
        else:
            x = box_left + (box_width - text_w) / 2
        draw.text((x, current_y), line, font=font, fill=fill)
        if underline:
            underline_y = current_y + line_h + underline_offset
            draw.line((x, underline_y, x + text_w, underline_y), fill=fill, width=underline_thickness)
        current_y += line_h
        if idx < len(lines) - 1:
            current_y += line_spacing
    return current_y


def text_block_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, line_count: int, line_spacing: int) -> int:
    if line_count <= 0:
        return 0
    line_h = text_line_height(draw, font)
    return (line_count * line_h) + (max(0, line_count - 1) * line_spacing)


def render_label_image(text1: str, text2: str, text3: str, footer: str, opts: Dict, preview: bool) -> Image.Image:
    layout = effective_layout(opts)
    qr_payload = build_qr_payload(text1, text2, text3, opts)

    requested_w = layout["requested_width_dots"]
    requested_h = layout["requested_height_dots"]
    pw = layout["effective_width_dots"]
    canvas_width = requested_w if preview else pw
    canvas_height = requested_h

    img = Image.new("RGB", (canvas_width, canvas_height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    if preview and requested_w > pw:
        draw.rectangle((pw, 0, requested_w - 1, requested_h - 1), fill=(244, 244, 244))
        draw.line((pw, 0, pw, requested_h), fill=(180, 180, 180), width=1)
        draw.rectangle((0, 0, requested_w - 1, requested_h - 1), outline=(205, 205, 205), width=2)

    qr_size = min(layout["qr_size_dots"], pw)
    qr_left = max((pw - qr_size) // 2, 0)
    qr_top = layout["top_margin_dots"]
    qr_img = build_qr_image(qr_payload, qr_size, opts).convert("RGB")
    img.paste(qr_img, (qr_left, qr_top))

    if preview:
        preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
        draw.rectangle(
            (qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1),
            outline=(220, 38, 38),
            width=preview_border_width,
        )

    margin_x = max((pw - qr_size) // 2, mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM))
    text_width = max(1, pw - (margin_x * 2))
    current_y = qr_top + qr_size + mm_to_dots(8)

    text_values = {1: text1, 2: text2, 3: text3}
    for idx in range(1, FIELD_COUNT + 1):
        cfg = get_field_config(opts, idx)
        font, lines, resolved_font_size = fit_field_lines(draw, text_values[idx], cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            margin_x,
            text_width,
            font,
            cfg["alignment"],
            cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += cfg["gap_after_dots"]

    footer_text = (footer or "").strip()
    if footer_text:
        footer_cfg = get_footer_config(opts)
        font, lines, resolved_font_size = fit_field_lines(draw, footer_text, footer_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        footer_height = text_block_height(draw, font, len(lines), line_spacing)
        footer_y = max(0, canvas_height - footer_height)
        if footer_y < current_y:
            LOGGER.warning(
                "Footer overlaps content: footer_y=%s current_y=%s footer_height=%s label_height=%s",
                footer_y,
                current_y,
                footer_height,
                canvas_height,
            )
        draw_aligned_text_lines(
            draw,
            lines,
            footer_y,
            margin_x,
            text_width,
            font,
            footer_cfg["alignment"],
            footer_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )

    return img


def build_zpl(text1: str, text2: str, text3: str, footer: str, copies: int, opts: Dict) -> str:
    layout = effective_layout(opts)
    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    label_img = render_label_image(text1, text2, text3, footer, opts, preview=False).convert("1")
    total_bytes, bytes_per_row, graphic_hex = image_to_gfa(label_img)
    return f"""^XA
^CI28
^PW{pw}
^LL{ll}
^LH0,0
^FO0,0^GFA,{total_bytes},{total_bytes},{bytes_per_row},{graphic_hex}^FS
^PQ{copies},0,1,N
^XZ"""


def send_to_printer(host: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    LOGGER.info("Sending %s bytes to printer %s:%s", len(data), host, port)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(data)
    LOGGER.info("Finished sending label payload to printer %s:%s", host, port)


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
        field1_default_value=opts["field1_default_value"],
        field2_default_value=opts["field2_default_value"],
        field3_default_value=opts["field3_default_value"],
        field1_style_summary=field_style_summary(opts, 1),
        field2_style_summary=field_style_summary(opts, 2),
        field3_style_summary=field_style_summary(opts, 3),
        footer_label=opts["footer_label"],
        footer_default_value=opts["footer_default_value"],
        footer_style_summary=footer_style_summary(opts),
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
    return None


@APP.route("/", methods=["GET"])
def index():
    opts = load_options()
    form = form_data_from_request(opts)
    LOGGER.info("Opened UI for printer %s:%s", opts["printer_host"], opts["printer_port"])
    return render_page(form, opts, result=None)


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_options()
    defaults = default_form_from_options(opts)
    text1 = request.form.get("text1", defaults["text1"]).strip()
    text2 = request.form.get("text2", defaults["text2"]).strip()
    text3 = request.form.get("text3", defaults["text3"]).strip()
    footer = request.form.get("footer", defaults["footer"]).strip()
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
        zpl = build_zpl(text1, text2, text3, footer, copies, opts)
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

    form = {"text1": text1, "text2": text2, "text3": text3, "footer": footer, "copies": copies_raw or DEFAULT_FORM["copies"]}
    return render_page(form, opts, result=result)


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_options()
    defaults = default_form_from_options(opts)
    text1 = request.args.get("text1", defaults["text1"])
    text2 = request.args.get("text2", defaults["text2"])
    text3 = request.args.get("text3", defaults["text3"])
    footer = request.args.get("footer", defaults["footer"])
    copies = max(1, min(50, int(request.args.get("copies", DEFAULT_FORM["copies"]))))
    zpl = build_zpl(text1, text2, text3, footer, copies, opts)
    LOGGER.info("Generated ZPL preview for copies=%s", copies)
    return Response(zpl, mimetype="text/plain; charset=utf-8")


@APP.route("/preview.png", methods=["GET"])
def preview_png():
    opts = load_options()
    defaults = default_form_from_options(opts)
    text1 = request.args.get("text1", defaults["text1"])
    text2 = request.args.get("text2", defaults["text2"])
    text3 = request.args.get("text3", defaults["text3"])
    footer = request.args.get("footer", defaults["footer"])
    LOGGER.info("Generating PNG preview for payload inputs text1=%r text2=%r text3=%r footer=%r", text1, text2, text3, footer)
    img = render_label_image(text1, text2, text3, footer, opts, preview=True)
    bio = BytesIO()
    img.save(bio, format="PNG", dpi=(203, 203), optimize=True)
    bio.seek(0)
    return send_file(bio, mimetype="image/png", download_name="label-preview.png")


@APP.route("/api/print", methods=["POST"])
def api_print():
    opts = load_options()
    defaults = default_form_from_options(opts)
    payload = request.get_json(force=True, silent=False) or {}
    text1 = str(payload.get("text1", defaults["text1"])).strip()
    text2 = str(payload.get("text2", defaults["text2"])).strip()
    text3 = str(payload.get("text3", defaults["text3"])).strip()
    footer = str(payload.get("footer", defaults["footer"])).strip()
    copies = max(1, min(50, int(payload.get("copies", 1))))
    if not text1 or not text2 or not text3:
        return jsonify({
            "ok": False,
            "error": f"{opts['field1_label']}, {opts['field2_label']}, and {opts['field3_label']} are required.",
        }), 400
    try:
        zpl = build_zpl(text1, text2, text3, footer, copies, opts)
        LOGGER.info("API print request received: copies=%s footer=%r", copies, footer)
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
