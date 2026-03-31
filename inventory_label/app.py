import json
import logging
import os
import re
import socket
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from typing import Dict, List, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import qrcode
import yaml
from flask import Flask, Response, jsonify, render_template_string, request, send_file
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

LOGGER = logging.getLogger("inventory_label")
APP = Flask(__name__)
APP.logger.handlers.clear()
APP.logger.propagate = True

DOTS_PER_MM = 203 / 25.4
PRINTER_MAX_WIDTH_DOTS = 1344
INGRESS_ALLOWED_IP = "172.30.32.2"
LOCAL_ALLOWED_IPS = {"127.0.0.1", "::1", None}
OPTIONS_PATH = "/data/options.json"
DEFAULT_TEXT_BLOCK_MARGIN_MM = 8.0
FIELD_GAP_MM = 4.0
FOOTER_GAP_MM = 3.0
SUPPORTED_UI_LANGUAGES = {"en", "de"}
SUPPORTED_ROTATIONS = {0, 90, 270}
ALIGNMENTS = {"left", "center", "right"}
FONT_FAMILIES = {"sans", "serif", "mono"}
FIELD_POSITIONS = {"body", "footer"}

DEFAULT_PROFILES_YAML = """\
- id: standard
  name: Standard
  printer_host: 10.50.20.12
  printer_port: 9100
  label_width_mm: 170
  label_height_mm: 305
  qr_size_mm: 170
  top_margin_mm: 0
  footer_bottom_margin_mm: 4
  print_rotation_degrees: 0
  qr_default_value: ""
  qr_quiet_zone_modules: 3
  qr_error_correction: M
  fields:
    - id: project_no
      name: Projektnummer
      default_value: "250001"
      alignment: center
      font_family: sans
      font_size_mm: 18
      bold: true
      italic: false
      underline: false
      print_by_default: true
      required: true
      number_only: true
      position: body
    - id: project_name
      name: Projektname
      default_value: EFH Huggentobbler Biel
      alignment: center
      font_family: sans
      font_size_mm: 13
      bold: false
      italic: false
      underline: false
      print_by_default: true
      position: body
    - id: element
      name: Element
      default_value: DE1
      alignment: center
      font_family: sans
      font_size_mm: 18
      bold: false
      italic: false
      underline: false
      print_by_default: true
      position: body
    - id: weight
      name: Gewicht
      default_value: ""
      alignment: center
      font_family: sans
      font_size_mm: 7
      bold: false
      italic: false
      underline: false
      print_by_default: false
      number_only: true
      suffix: kg
      position: body
    - id: footer
      name: Footer
      default_value: Ernst Fink AG, Schorenweg 144, 4585 Biezwil
      alignment: center
      font_family: sans
      font_size_mm: 5
      bold: false
      italic: false
      underline: false
      print_by_default: true
      position: footer
      append_current_date: true
"""

DEFAULT_OPTIONS = {
    "ui_language": "de",
    "label_profiles_yaml": DEFAULT_PROFILES_YAML,
}

QR_ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

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

UI_STRINGS = {
    "en": {
        "lang": "en",
        "page_title": "Inventory Label",
        "intro_text": "Choose a label profile from the add-on configuration, enter the QR value and the configured field values, then preview and print.",
        "profile_select": "Label profile",
        "profile_none": "(none)",
        "qr_value_label": "QR value",
        "copies": "Copies",
        "configured_printer": "Configured printer",
        "print_label_button": "Print label",
        "preview_zpl": "Preview ZPL",
        "open_png_preview": "Open PNG preview",
        "preview_heading": "Preview",
        "preview_alt": "Label preview",
        "preview_meta": "PNG is rendered from the same layout coordinates used for print generation and exported at 203 dpi. Portrait preview tries to match the configured label size in mm. Horizontal preview keeps aspect ratio and fits to the available width. The red outline shows the full QR footprint including the configured quiet zone.",
        "fields_heading": "Configured fields",
        "print_field": "Print",
        "required": "Required",
        "numeric_only": "Numbers only",
        "position": "Position",
        "position_body": "Body",
        "position_footer": "Footer",
        "configured_label_mapping": "Active profile summary",
        "profile_active": "Active profile",
        "current_qr_payload": "Current QR payload",
        "requested_label": "Requested label",
        "requested_qr": "Requested QR",
        "effective_print_width": "Effective print width on ZT420/ZT421 @ 203 dpi",
        "print_rotation": "Print rotation",
        "width_warning": "Requested width exceeds the printer's 168 mm printable width. The add-on clamps the printed width automatically.",
        "sent_labels_message": "Sent {copies} label(s) to {host}:{port}. QR payload: {qr_payload}",
        "print_failed_message": "Print failed: {error}",
        "preview_failed_message": "Preview failed: {error}",
        "field_required": "{field} is required.",
        "field_numbers_only": "{field} must contain numbers only.",
        "configuration_error": "Configuration error: {error}",
        "unknown_error": "Unknown error",
        "none": "(none)",
    },
    "de": {
        "lang": "de",
        "page_title": "Inventory Label",
        "intro_text": "Wähle ein Etikettenprofil aus der Add-on-Konfiguration, gib den QR-Inhalt und die konfigurierten Feldwerte ein und drucke danach das Etikett.",
        "profile_select": "Etikettenprofil",
        "profile_none": "(keins)",
        "qr_value_label": "QR-Inhalt",
        "copies": "Anzahl",
        "configured_printer": "Konfigurierter Drucker",
        "print_label_button": "Etikett drucken",
        "preview_zpl": "ZPL-Vorschau",
        "open_png_preview": "PNG-Vorschau öffnen",
        "preview_heading": "Vorschau",
        "preview_alt": "Etikettenvorschau",
        "preview_meta": "Die PNG-Vorschau wird aus denselben Layout-Koordinaten wie der Druck erstellt und mit 203 dpi exportiert. Hochformat versucht die konfigurierte Labelgröße in mm abzubilden. Querformat behält das Seitenverhältnis bei und passt sich an die verfügbare Breite an. Der rote Rahmen zeigt die gesamte QR-Fläche inklusive Quiet Zone.",
        "fields_heading": "Konfigurierte Felder",
        "print_field": "Drucken",
        "required": "Pflichtfeld",
        "numeric_only": "Nur Zahlen",
        "position": "Position",
        "position_body": "Inhalt",
        "position_footer": "Footer",
        "configured_label_mapping": "Zusammenfassung des aktiven Profils",
        "profile_active": "Aktives Profil",
        "current_qr_payload": "Aktueller QR-Inhalt",
        "requested_label": "Gewünschtes Label",
        "requested_qr": "Gewünschter QR",
        "effective_print_width": "Effektive Druckbreite auf ZT420/ZT421 @ 203 dpi",
        "print_rotation": "Drehung",
        "width_warning": "Die gewünschte Breite überschreitet die druckbare Breite von 168 mm. Das Add-on begrenzt die Druckbreite automatisch.",
        "sent_labels_message": "{copies} Etikett(en) an {host}:{port} gesendet. QR-Inhalt: {qr_payload}",
        "print_failed_message": "Druck fehlgeschlagen: {error}",
        "preview_failed_message": "Vorschau fehlgeschlagen: {error}",
        "field_required": "{field} ist erforderlich.",
        "field_numbers_only": "{field} darf nur Zahlen enthalten.",
        "configuration_error": "Konfigurationsfehler: {error}",
        "unknown_error": "Unbekannter Fehler",
        "none": "(keins)",
    },
}

HTML = """
<!doctype html>
<html lang="{{ ui.lang }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ ui.page_title }}</title>
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
    body { margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); margin-bottom: 20px; }
    h1, h2, h3 { margin-top: 0; }
    label { display: block; font-weight: 600; margin-bottom: 8px; }
    input, select { width: 100%; box-sizing: border-box; border-radius: 12px; border: 1px solid var(--border); background: #0f172a; color: var(--text); padding: 12px 14px; font: inherit; margin-bottom: 16px; }
    input[type="checkbox"] { width: auto; margin: 0; accent-color: var(--accent); }
    .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
    .btns { display: flex; gap: 12px; flex-wrap: wrap; }
    button, .button-link { border: none; background: var(--accent); color: white; padding: 12px 18px; border-radius: 12px; font: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
    .secondary { background: transparent; border: 1px solid var(--border); }
    .flash { border-radius: 12px; padding: 14px 16px; margin-bottom: 16px; }
    .flash.ok { background: rgba(16,185,129,0.14); border: 1px solid var(--ok); }
    .flash.error { background: rgba(239,68,68,0.14); border: 1px solid var(--danger); }
    .muted { color: var(--muted); }
    .preview-wrap { overflow: auto; background: #0b1220; border: 1px solid var(--border); border-radius: 16px; padding: 16px; }
    .preview-stage { display: flex; justify-content: center; align-items: flex-start; min-width: 0; width: 100%; }
    .preview-frame { width: {{ preview_display_width_mm }}mm; height: {{ preview_display_height_mm }}mm; flex: 0 0 auto; max-width: none; background: var(--label-bg); border: 1px solid var(--label-edge); box-shadow: 0 10px 30px rgba(0,0,0,0.28); }
    .preview-frame img { display: block; width: 100%; height: 100%; object-fit: contain; background: white; }
    .preview-meta { margin-top: 12px; font-size: 0.95rem; color: var(--muted); }
    .config-list { margin: 0; padding-left: 18px; color: var(--muted); }
    .config-list li + li { margin-top: 8px; }
    .field-grid { display: grid; gap: 14px; }
    .field-card { background: #111827; border: 1px solid var(--border); border-radius: 14px; padding: 14px; }
    .field-card h3 { margin-bottom: 10px; font-size: 1rem; }
    .field-meta { display: flex; flex-wrap: wrap; gap: 12px; color: var(--muted); font-size: 0.92rem; margin-bottom: 12px; }
    .checkline { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{{ ui.page_title }}</h1>
      <p class="muted">{{ ui.intro_text }}</p>
      {% if result %}
        <div class="flash {{ 'ok' if result.success else 'error' }}">{{ result.message }}</div>
      {% endif %}
      <form id="label-form" method="post" action="{{ ingress_base }}/print">
        <label for="profile_id">{{ ui.profile_select }}</label>
        <select id="profile_id" name="profile_id">
          {% for profile in label_profiles %}
            <option value="{{ profile.id }}" {% if profile.id == active_profile_id %}selected{% endif %}>{{ profile.name }}</option>
          {% endfor %}
        </select>

        <label for="qr_value">{{ ui.qr_value_label }}</label>
        <input id="qr_value" name="qr_value" type="text" value="{{ form.qr_value }}" required>

        <h2>{{ ui.fields_heading }}</h2>
        <div class="field-grid">
          {% for field in field_forms %}
          <div class="field-card">
            <h3>{{ field.name }}</h3>
            <div class="field-meta">
              <span>{{ ui.position }}: {{ ui.position_footer if field.position == 'footer' else ui.position_body }}</span>
              {% if field.required %}<span>{{ ui.required }}</span>{% endif %}
              {% if field.number_only %}<span>{{ ui.numeric_only }}</span>{% endif %}
            </div>
            <div class="checkline">
              <input id="print_{{ field.id }}" name="print_{{ field.id }}" type="checkbox" value="1" {% if field.print_enabled %}checked{% endif %}>
              <label for="print_{{ field.id }}" style="margin:0; font-weight:500;">{{ ui.print_field }}</label>
            </div>
            <input
              id="field_{{ field.id }}"
              name="field_{{ field.id }}"
              type="text"
              value="{{ field.value }}"
              {% if field.number_only %}inputmode="numeric" pattern="[0-9]*" data-number-only="1"{% endif %}
            >
          </div>
          {% endfor %}
        </div>

        <div class="row">
          <div>
            <label for="copies">{{ ui.copies }}</label>
            <input id="copies" name="copies" type="number" min="1" max="50" value="{{ form.copies }}" required>
          </div>
          <div>
            <label>{{ ui.configured_printer }}</label>
            <input value="{{ printer_host }}:{{ printer_port }}" disabled>
          </div>
        </div>

        <div class="btns">
          <button type="submit">{{ ui.print_label_button }}</button>
          <a id="preview-zpl-link" class="button-link secondary" href="{{ ingress_base }}/preview?{{ preview_query }}">{{ ui.preview_zpl }}</a>
          <a id="preview-png-link" class="button-link secondary" href="{{ ingress_base }}/preview.png?{{ preview_query }}" target="_blank" rel="noopener">{{ ui.open_png_preview }}</a>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>{{ ui.preview_heading }}</h2>
      <div class="preview-wrap">
        <div class="preview-stage">
          <div class="preview-frame">
            <img id="preview-image" src="{{ ingress_base }}/preview.png?{{ preview_query }}" alt="{{ ui.preview_alt }}">
          </div>
        </div>
      </div>
      <div class="preview-meta">{{ ui.preview_meta }}</div>
    </div>

    <div class="card">
      <h2>{{ ui.configured_label_mapping }}</h2>
      <ul class="config-list">
        <li><strong>{{ ui.profile_active }}:</strong> <code>{{ active_profile_name or ui.profile_none }}</code></li>
        <li><strong>{{ ui.current_qr_payload }}:</strong> <code>{{ qr_preview or ui.none }}</code></li>
        <li><strong>{{ ui.requested_label }}:</strong> <code>{{ requested_width_mm }} × {{ requested_height_mm }} mm</code></li>
        <li><strong>{{ ui.requested_qr }}:</strong> <code>{{ requested_qr_mm }} × {{ requested_qr_mm }} mm</code></li>
        <li><strong>QR:</strong> <code>quiet zone {{ qr_quiet_zone_modules }}, ECC {{ qr_error_correction }}</code></li>
        <li><strong>{{ ui.print_rotation }}:</strong> <code>{{ print_rotation_degrees }}°</code></li>
        <li><strong>{{ ui.effective_print_width }}:</strong> <code>{{ effective_width_mm }} mm ({{ effective_width_dots }} dots)</code></li>
      </ul>
      {% if width_warning %}
      <p class="muted">{{ ui.width_warning }}</p>
      {% endif %}
    </div>
  </div>

  <script>
    (function () {
      const form = document.getElementById("label-form");
      const profileSelect = document.getElementById("profile_id");
      const previewImage = document.getElementById("preview-image");
      const previewFrame = document.querySelector(".preview-frame");
      const previewWrap = document.querySelector(".preview-wrap");
      const previewStage = document.querySelector(".preview-stage");
      const previewPngLink = document.getElementById("preview-png-link");
      const previewZplLink = document.getElementById("preview-zpl-link");
      if (!form || !previewImage || !previewFrame || !previewWrap || !previewStage || !previewPngLink || !previewZplLink) return;

      let refreshTimer = null;
      let previewNonce = Date.now();
      const ingressBase = {{ ingress_base|tojson }};
      const portraitWidthMm = {{ preview_display_width_mm|tojson }};
      const portraitHeightMm = {{ preview_display_height_mm|tojson }};

      function sanitizeNumericInput(input) {
        if (!input || input.dataset.numberOnly !== "1") return;
        const cleaned = (input.value || "").replace(/\\D+/g, "");
        if (cleaned !== input.value) input.value = cleaned;
      }

      function normalizedCopies() {
        const input = document.getElementById("copies");
        const raw = parseInt((input && input.value) || "1", 10);
        if (Number.isNaN(raw)) return "1";
        return String(Math.max(1, Math.min(50, raw)));
      }

      function buildQuery() {
        const params = new URLSearchParams();
        const formData = new FormData(form);
        for (const [key, value] of formData.entries()) {
          if (key === "copies") continue;
          params.set(key, String(value));
        }
        params.set("copies", normalizedCopies());
        return params;
      }

      function syncPreviewFrameToImage() {
        const naturalWidth = previewImage.naturalWidth || 0;
        const naturalHeight = previewImage.naturalHeight || 0;
        if (!naturalWidth || !naturalHeight) return;
        const wrapStyles = window.getComputedStyle(previewWrap);
        const horizontalPadding = (parseFloat(wrapStyles.paddingLeft || "0") || 0) + (parseFloat(wrapStyles.paddingRight || "0") || 0);
        const availableWidth = Math.max(160, Math.floor(previewWrap.clientWidth - horizontalPadding - 2));
        if (naturalWidth >= naturalHeight) {
          const scaledHeight = Math.max(1, Math.round((availableWidth * naturalHeight) / naturalWidth));
          previewWrap.style.overflowX = "hidden";
          previewStage.style.width = "100%";
          previewFrame.style.width = `${availableWidth}px`;
          previewFrame.style.height = `${scaledHeight}px`;
        } else {
          previewWrap.style.overflowX = "auto";
          previewStage.style.width = "100%";
          previewFrame.style.width = `${portraitWidthMm}mm`;
          previewFrame.style.height = `${portraitHeightMm}mm`;
        }
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

      if (profileSelect) {
        profileSelect.addEventListener("change", () => {
          const url = new URL(`${ingressBase}/`, window.location.origin);
          if (profileSelect.value) url.searchParams.set("profile_id", profileSelect.value);
          window.location.href = url.toString();
        });
      }

      form.querySelectorAll("input, select").forEach((input) => {
        if (input.dataset && input.dataset.numberOnly === "1") {
          input.addEventListener("input", () => { sanitizeNumericInput(input); schedulePreviewUpdate(); });
          input.addEventListener("change", () => { sanitizeNumericInput(input); applyPreviewUpdate(); });
          return;
        }
        if (input.type === "checkbox") {
          input.addEventListener("click", applyPreviewUpdate);
        } else {
          input.addEventListener("input", schedulePreviewUpdate);
          input.addEventListener("change", applyPreviewUpdate);
        }
      });

      previewImage.addEventListener("load", syncPreviewFrameToImage);
      window.addEventListener("resize", syncPreviewFrameToImage);
      applyPreviewUpdate();
    })();
  </script>
</body>
</html>
"""


def ingress_base_path() -> str:
    base = request.headers.get("X-Ingress-Path") or request.script_root or ""
    return base.rstrip("/")


def normalize_string(value: object, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else default


def normalize_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def normalize_int(value: object, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def normalize_float(value: object, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(str(value).strip())
    except Exception:
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def normalize_ui_language(value: object, default: str = "de") -> str:
    lang = normalize_string(value, default).lower()
    return lang if lang in SUPPORTED_UI_LANGUAGES else default


def normalize_alignment(value: object, default: str = "center") -> str:
    alignment = normalize_string(value, default).lower()
    return alignment if alignment in ALIGNMENTS else default


def normalize_font_family(value: object, default: str = "sans") -> str:
    family = normalize_string(value, default).lower()
    return family if family in FONT_FAMILIES else default


def normalize_rotation_degrees(value: object, default: int = 0) -> int:
    rotation = normalize_int(value, default)
    return rotation if rotation in SUPPORTED_ROTATIONS else default


def normalize_position(value: object, default: str = "body") -> str:
    pos = normalize_string(value, default).lower()
    return pos if pos in FIELD_POSITIONS else default


def sanitize_id(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", value.strip().lower()).strip("_")
    return normalized or fallback


def get_ui_strings(language: object) -> Dict[str, str]:
    lang = normalize_ui_language(language, DEFAULT_OPTIONS["ui_language"])
    ui = dict(UI_STRINGS["en"])
    ui.update(UI_STRINGS.get(lang, {}))
    return ui


def ui_text(language_or_options: object, key: str, **kwargs) -> str:
    if isinstance(language_or_options, dict):
        language = language_or_options.get("ui_language", DEFAULT_OPTIONS["ui_language"])
    else:
        language = language_or_options
    template = get_ui_strings(language).get(key, UI_STRINGS["en"].get(key, key))
    return template.format(**kwargs)


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
    options["ui_language"] = normalize_ui_language(options.get("ui_language"), DEFAULT_OPTIONS["ui_language"])
    raw_profiles = options.get("label_profiles_yaml")
    if raw_profiles is None:
        raw_profiles = DEFAULT_OPTIONS["label_profiles_yaml"]
    options["label_profiles_yaml"] = str(raw_profiles)
    return options


def normalize_profile_field(raw: object, idx: int) -> Dict:
    data = raw if isinstance(raw, dict) else {}
    name = normalize_string(data.get("name"), f"Field {idx}")
    field_id = sanitize_id(str(data.get("id") or name), f"field_{idx}")
    return {
        "id": field_id,
        "name": name,
        "default_value": "" if data.get("default_value") is None else str(data.get("default_value")),
        "alignment": normalize_alignment(data.get("alignment"), "center"),
        "font_family": normalize_font_family(data.get("font_family"), "sans"),
        "font_size_mm": normalize_float(data.get("font_size_mm"), 7.0, 2.0, 30.0),
        "bold": normalize_bool(data.get("bold"), False),
        "italic": normalize_bool(data.get("italic"), False),
        "underline": normalize_bool(data.get("underline"), False),
        "print_by_default": normalize_bool(data.get("print_by_default"), True),
        "required": normalize_bool(data.get("required"), False),
        "number_only": normalize_bool(data.get("number_only"), False),
        "suffix": str(data.get("suffix") or "").strip(),
        "position": normalize_position(data.get("position"), "body"),
        "append_current_date": normalize_bool(data.get("append_current_date"), False),
        "max_lines": normalize_int(data.get("max_lines"), 3, 1, 8),
    }


def parse_label_profiles(raw: object) -> List[Dict]:
    source = raw if raw not in (None, "") else DEFAULT_OPTIONS["label_profiles_yaml"]
    if isinstance(source, str):
        try:
            data = yaml.safe_load(source) if source.strip() else []
        except Exception as exc:
            LOGGER.warning("Failed to parse label profiles YAML: %s", exc)
            data = []
    else:
        data = source
    if isinstance(data, dict):
        for key in ("label_profiles", "profiles", "labels"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []

    profiles: List[Dict] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        name = normalize_string(item.get("name"), f"Label {idx}")
        profile_id = sanitize_id(str(item.get("id") or name), f"label_{idx}")
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        normalized_fields = [normalize_profile_field(field, field_idx) for field_idx, field in enumerate(fields, start=1)]
        profiles.append({
            "id": profile_id,
            "name": name,
            "printer_host": normalize_string(item.get("printer_host"), "10.50.20.12"),
            "printer_port": normalize_int(item.get("printer_port"), 9100, 1, 65535),
            "label_width_mm": normalize_float(item.get("label_width_mm"), 170.0, 50.0, 500.0),
            "label_height_mm": normalize_float(item.get("label_height_mm"), 305.0, 50.0, 1000.0),
            "qr_size_mm": normalize_float(item.get("qr_size_mm"), 170.0, 10.0, 300.0),
            "top_margin_mm": normalize_float(item.get("top_margin_mm"), 0.0, 0.0, 100.0),
            "footer_bottom_margin_mm": normalize_float(item.get("footer_bottom_margin_mm"), 0.0, 0.0, 50.0),
            "print_rotation_degrees": normalize_rotation_degrees(item.get("print_rotation_degrees"), 0),
            "qr_default_value": "" if item.get("qr_default_value") is None else str(item.get("qr_default_value")),
            "qr_quiet_zone_modules": normalize_int(item.get("qr_quiet_zone_modules"), 3, 0, 20),
            "qr_error_correction": str(item.get("qr_error_correction") or "M").strip().upper() if str(item.get("qr_error_correction") or "M").strip().upper() in QR_ERROR_CORRECTION_MAP else "M",
            "fields": normalized_fields,
        })
    return profiles


def load_runtime_options(profile_id: str | None = None) -> Dict:
    opts = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles_yaml"))
    if not profiles:
        profiles = parse_label_profiles(DEFAULT_OPTIONS["label_profiles_yaml"])
    selected_id = profile_id or request.values.get("profile_id") or request.args.get("profile_id") or request.form.get("profile_id")
    active_profile = None
    if selected_id:
        active_profile = next((profile for profile in profiles if profile["id"] == selected_id), None)
    if active_profile is None and profiles:
        active_profile = profiles[0]
    opts["label_profiles"] = profiles
    opts["active_profile"] = active_profile
    opts["active_profile_id"] = active_profile["id"] if active_profile else ""
    opts["active_profile_name"] = active_profile["name"] if active_profile else ""
    return opts


def field_value_name(field_id: str) -> str:
    return f"field_{field_id}"


def field_print_name(field_id: str) -> str:
    return f"print_{field_id}"


def build_field_forms(profile: Dict, source: Dict | None = None) -> List[Dict]:
    source = source or {}
    forms: List[Dict] = []
    for field in profile.get("fields", []):
        value_key = field_value_name(field["id"])
        print_key = field_print_name(field["id"])
        value = source.get(value_key)
        if value is None:
            value = field["default_value"]
        print_raw = source.get(print_key)
        print_enabled = field["print_by_default"] if print_raw is None else normalize_bool(print_raw, field["print_by_default"])
        forms.append({**field, "value": str(value), "print_enabled": print_enabled})
    return forms


def default_form_from_profile(profile: Dict | None) -> Dict[str, str]:
    form = {
        "profile_id": profile["id"] if profile else "",
        "qr_value": profile.get("qr_default_value", "") if profile else "",
        "copies": "1",
    }
    for field in (profile or {}).get("fields", []):
        form[field_value_name(field["id"])] = field["default_value"]
        if field["print_by_default"]:
            form[field_print_name(field["id"])] = "1"
    return form


def form_data_from_request(opts: Dict) -> Tuple[Dict[str, str], List[Dict]]:
    profile = opts.get("active_profile") or {}
    defaults = default_form_from_profile(profile)
    form = {
        "profile_id": request.values.get("profile_id", defaults.get("profile_id", "")),
        "qr_value": request.values.get("qr_value", defaults.get("qr_value", "")),
        "copies": request.values.get("copies", defaults.get("copies", "1")),
    }
    field_forms = build_field_forms(profile, request.values)
    for field in field_forms:
        form[field_value_name(field["id"])] = field["value"]
        if field["print_enabled"]:
            form[field_print_name(field["id"])] = "1"
    return form, field_forms


def validate_required_text(value: object, label: str, language: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(ui_text(language, "field_required", field=label))
    return text


def validate_field_forms(field_forms: List[Dict], language: str) -> List[Dict]:
    validated: List[Dict] = []
    for field in field_forms:
        value = str(field.get("value") or "").strip()
        if field.get("number_only") and value and not value.isdigit():
            raise ValueError(ui_text(language, "field_numbers_only", field=field["name"]))
        if field.get("required") and field.get("print_enabled"):
            value = validate_required_text(value, field["name"], language)
        validated.append({**field, "value": value})
    return validated


def current_label_date_str() -> str:
    tz_name = os.environ.get("TZ") or "Europe/Zurich"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
    return now.strftime("%d.%m.%Y")


def apply_field_text_transform(field: Dict) -> str:
    text = str(field.get("value") or "").strip()
    if not text:
        return ""
    suffix = str(field.get("suffix") or "").strip()
    if suffix:
        text = f"{text} {suffix}"
    if field.get("append_current_date"):
        text = f"{text} - {current_label_date_str()}"
    return text


def fields_to_blocks(field_forms: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    body: List[Dict] = []
    footer: List[Dict] = []
    for field in field_forms:
        if not field.get("print_enabled"):
            continue
        text = apply_field_text_transform(field)
        if not text:
            continue
        block = {
            "value": text,
            "alignment": field["alignment"],
            "font_family": field["font_family"],
            "font_size_mm": field["font_size_mm"],
            "bold": field["bold"],
            "italic": field["italic"],
            "underline": field["underline"],
            "max_lines": field["max_lines"],
        }
        if field.get("position") == "footer":
            footer.append(block)
        else:
            body.append(block)
    return body, footer


def mm_to_dots(mm_value: float) -> int:
    return max(1, int(round(float(mm_value) * DOTS_PER_MM)))


def dots_to_mm(dots: int) -> float:
    return round(dots / DOTS_PER_MM, 1)


def effective_layout(profile: Dict) -> Dict:
    requested_width_dots = mm_to_dots(profile["label_width_mm"])
    requested_height_dots = mm_to_dots(profile["label_height_mm"])
    qr_size_dots = mm_to_dots(profile["qr_size_mm"])
    top_margin_dots = mm_to_dots(profile["top_margin_mm"])
    footer_bottom_margin_dots = mm_to_dots(profile.get("footer_bottom_margin_mm", 0.0))
    effective_width_dots = min(requested_width_dots, PRINTER_MAX_WIDTH_DOTS)
    return {
        "requested_width_dots": requested_width_dots,
        "requested_height_dots": requested_height_dots,
        "qr_size_dots": qr_size_dots,
        "top_margin_dots": top_margin_dots,
        "footer_bottom_margin_dots": footer_bottom_margin_dots,
        "effective_width_dots": effective_width_dots,
        "width_warning": requested_width_dots > PRINTER_MAX_WIDTH_DOTS,
    }


def qr_error_correction_constant(profile: Dict) -> int:
    return QR_ERROR_CORRECTION_MAP.get(profile.get("qr_error_correction", "M"), QR_ERROR_CORRECTION_MAP["M"])


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


def build_qr_image(data: str, size_dots: int, profile: Dict) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qr_error_correction_constant(profile),
        box_size=10,
        border=normalize_int(profile.get("qr_quiet_zone_modules"), 3, 0, 20),
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")
    return img.resize((size_dots, size_dots), Image.Resampling.NEAREST)


def image_to_gfa(img: Image.Image) -> Tuple[int, int, str]:
    if img.mode != "1":
        img = img.convert("1")
    width, height = img.size
    bytes_per_row = (width + 7) // 8
    total_bytes = bytes_per_row * height
    pixels = img.load()
    rows: List[str] = []
    for y in range(height):
        row_bytes: List[int] = []
        for byte_idx in range(bytes_per_row):
            value = 0
            for bit in range(8):
                x = (byte_idx * 8) + bit
                value <<= 1
                if x < width and pixels[x, y] == 0:
                    value |= 1
            row_bytes.append(value)
        rows.append("".join(f"{item:02X}" for item in row_bytes))
    return total_bytes, bytes_per_row, "".join(rows)


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


def fit_block_lines(draw: ImageDraw.ImageDraw, text: str, block: Dict, max_width: int) -> Tuple[ImageFont.ImageFont, List[str], int]:
    start_size = max(10, mm_to_dots(block["font_size_mm"]))
    min_size = max(10, int(start_size * 0.6))
    best_font = load_font(block["font_family"], block["bold"], block["italic"], start_size)
    best_lines = wrap_text_lines(draw, text, best_font, max_width, normalize_int(block.get("max_lines"), 3, 1, 8))
    best_size = start_size
    for size in range(start_size, min_size - 1, -1):
        font = load_font(block["font_family"], block["bold"], block["italic"], size)
        lines = wrap_text_lines(draw, text, font, max_width, normalize_int(block.get("max_lines"), 3, 1, 8))
        best_font, best_lines, best_size = font, lines, size
        widths = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            widths.append(bbox[2] - bbox[0])
        if widths and max(widths) <= max_width:
            break
    return best_font, best_lines, best_size


def draw_aligned_lines(draw: ImageDraw.ImageDraw, lines: List[str], y: int, box_left: int, box_width: int, font: ImageFont.ImageFont, alignment: str, underline: bool, line_spacing: int) -> int:
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
        draw.text((x, current_y), line, font=font, fill=(0, 0, 0))
        if underline:
            underline_y = current_y + line_h + underline_offset
            draw.line((x, underline_y, x + text_w, underline_y), fill=(0, 0, 0), width=underline_thickness)
        current_y += line_h
        if idx < len(lines) - 1:
            current_y += line_spacing
    return current_y


def draw_body_blocks(draw: ImageDraw.ImageDraw, start_y: int, box_left: int, box_width: int, body_blocks: List[Dict]) -> int:
    current_y = start_y
    for block in body_blocks:
        font, lines, resolved = fit_block_lines(draw, block["value"], block, box_width)
        spacing = max(4, resolved // 7)
        current_y = draw_aligned_lines(draw, lines, current_y, box_left, box_width, font, block["alignment"], block["underline"], spacing)
        current_y += mm_to_dots(FIELD_GAP_MM)
    return current_y


def block_height(draw: ImageDraw.ImageDraw, block: Dict, box_width: int) -> Tuple[int, ImageFont.ImageFont, List[str], int]:
    font, lines, resolved = fit_block_lines(draw, block["value"], block, box_width)
    spacing = max(4, resolved // 7)
    line_h = text_line_height(draw, font)
    total = (line_h * len(lines)) + (max(0, len(lines) - 1) * spacing)
    return total, font, lines, spacing


def draw_footer_blocks(draw: ImageDraw.ImageDraw, bottom_y: int, box_left: int, box_width: int, footer_blocks: List[Dict]) -> int:
    current_bottom = bottom_y
    for block in reversed(footer_blocks):
        total_h, font, lines, spacing = block_height(draw, block, box_width)
        top_y = current_bottom - total_h
        draw_aligned_lines(draw, lines, top_y, box_left, box_width, font, block["alignment"], block["underline"], spacing)
        current_bottom = top_y - mm_to_dots(FOOTER_GAP_MM)
    return current_bottom


def draw_background_for_preview(img: Image.Image, requested_w: int, requested_h: int, printable_left: int, printable_w: int) -> None:
    draw = ImageDraw.Draw(img)
    content_right = printable_left + printable_w
    if printable_left > 0:
        draw.rectangle((0, 0, printable_left - 1, requested_h - 1), fill=(244, 244, 244))
    if content_right < requested_w:
        draw.rectangle((content_right, 0, requested_w - 1, requested_h - 1), fill=(244, 244, 244))
    draw.line((printable_left, 0, printable_left, requested_h), fill=(180, 180, 180), width=1)
    draw.line((content_right - 1, 0, content_right - 1, requested_h), fill=(180, 180, 180), width=1)
    draw.rectangle((0, 0, requested_w - 1, requested_h - 1), outline=(205, 205, 205), width=2)


def render_portrait_content(printable_w: int, canvas_h: int, qr_value: str, body_blocks: List[Dict], footer_blocks: List[Dict], profile: Dict, preview: bool) -> Image.Image:
    layout = effective_layout(profile)
    img = Image.new("RGB", (printable_w, canvas_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    qr_size = min(layout["qr_size_dots"], printable_w)
    qr_left = max((printable_w - qr_size) // 2, 0)
    qr_top = layout["top_margin_dots"]
    qr_img = build_qr_image(qr_value, qr_size, profile).convert("RGB")
    img.paste(qr_img, (qr_left, qr_top))
    if preview:
        preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
        draw.rectangle((qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1), outline=(220, 38, 38), width=preview_border_width)
    margin_x = max((printable_w - qr_size) // 2, mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM))
    text_width = max(1, printable_w - (margin_x * 2))
    current_y = qr_top + qr_size + mm_to_dots(8)
    draw_body_blocks(draw, current_y, margin_x, text_width, body_blocks)
    if footer_blocks:
        footer_bottom = canvas_h - layout["footer_bottom_margin_dots"]
        draw_footer_blocks(draw, footer_bottom, margin_x, text_width, footer_blocks)
    return img


def render_rotated_content(printable_w: int, canvas_h: int, qr_value: str, body_blocks: List[Dict], footer_blocks: List[Dict], profile: Dict, preview: bool, rotation_degrees: int) -> Image.Image:
    layout = effective_layout(profile)
    logical_w = canvas_h
    logical_h = printable_w
    landscape = Image.new("RGB", (logical_w, logical_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(landscape)
    qr_size = min(layout["qr_size_dots"], logical_h)
    qr_left = min(max(layout["top_margin_dots"], 0), max(0, logical_w - qr_size))
    qr_top = max((logical_h - qr_size) // 2, 0)
    qr_img = build_qr_image(qr_value, qr_size, profile).convert("RGB")
    landscape.paste(qr_img, (qr_left, qr_top))
    if preview:
        preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
        draw.rectangle((qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1), outline=(220, 38, 38), width=preview_border_width)
    inter_block_gap = mm_to_dots(8)
    right_margin = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    text_left = min(logical_w, qr_left + qr_size + inter_block_gap)
    text_width = max(1, logical_w - text_left - right_margin)
    text_top = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    draw_body_blocks(draw, text_top, text_left, text_width, body_blocks)
    if footer_blocks:
        footer_bottom = logical_h - layout["footer_bottom_margin_dots"]
        draw_footer_blocks(draw, footer_bottom, text_left, text_width, footer_blocks)
    if rotation_degrees == 90:
        return landscape.transpose(Image.Transpose.ROTATE_270)
    return landscape.transpose(Image.Transpose.ROTATE_90)


def orient_preview_for_display(img: Image.Image, rotation_degrees: int) -> Image.Image:
    if rotation_degrees == 90:
        return img.transpose(Image.Transpose.ROTATE_90)
    if rotation_degrees == 270:
        return img.transpose(Image.Transpose.ROTATE_270)
    return img


def render_label_image(qr_value: str, field_forms: List[Dict], profile: Dict, preview: bool) -> Image.Image:
    layout = effective_layout(profile)
    requested_w = layout["requested_width_dots"]
    requested_h = layout["requested_height_dots"]
    printable_w = layout["effective_width_dots"]
    rotation_degrees = profile["print_rotation_degrees"]
    body_blocks, footer_blocks = fields_to_blocks(field_forms)
    printable_image = render_portrait_content(printable_w, requested_h, qr_value, body_blocks, footer_blocks, profile, preview) if rotation_degrees == 0 else render_rotated_content(printable_w, requested_h, qr_value, body_blocks, footer_blocks, profile, preview, rotation_degrees)
    if not preview:
        return printable_image
    if requested_w <= printable_w:
        return orient_preview_for_display(printable_image, rotation_degrees)
    canvas = Image.new("RGB", (requested_w, requested_h), color=(255, 255, 255))
    printable_left = max((requested_w - printable_w) // 2, 0)
    draw_background_for_preview(canvas, requested_w, requested_h, printable_left, printable_w)
    canvas.paste(printable_image, (printable_left, 0))
    return orient_preview_for_display(canvas, rotation_degrees)


def build_zpl(qr_value: str, field_forms: List[Dict], copies: int, profile: Dict) -> str:
    layout = effective_layout(profile)
    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    label_img = render_label_image(qr_value, field_forms, profile, preview=False).convert("1")
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


def preview_query_from_form(form: Dict[str, str], field_forms: List[Dict]) -> str:
    params = {
        "profile_id": form.get("profile_id", ""),
        "qr_value": form.get("qr_value", ""),
        "copies": form.get("copies", "1"),
    }
    for field in field_forms:
        params[field_value_name(field["id"])] = field.get("value", "")
        if field.get("print_enabled"):
            params[field_print_name(field["id"])] = "1"
    return urlencode(params)


def render_page(form: Dict[str, str], opts: Dict, field_forms: List[Dict], result: Dict | None = None) -> str:
    profile = opts.get("active_profile") or {}
    layout = effective_layout(profile)
    ui = get_ui_strings(opts.get("ui_language"))
    preview_display_width_mm = profile.get("label_width_mm", 170.0)
    preview_display_height_mm = profile.get("label_height_mm", 305.0)
    if profile.get("print_rotation_degrees") in (90, 270):
        preview_display_width_mm, preview_display_height_mm = preview_display_height_mm, preview_display_width_mm
    try:
        qr_preview = validate_required_text(form.get("qr_value", ""), ui["qr_value_label"], opts["ui_language"])
    except Exception as exc:
        qr_preview = ui_text(opts, "configuration_error", error=exc)
    return render_template_string(
        HTML,
        ui=ui,
        result=result,
        form=form,
        field_forms=field_forms,
        label_profiles=opts.get("label_profiles", []),
        active_profile_id=opts.get("active_profile_id", ""),
        active_profile_name=opts.get("active_profile_name", ""),
        printer_host=profile.get("printer_host", ""),
        printer_port=profile.get("printer_port", ""),
        qr_preview=qr_preview,
        requested_width_mm=profile.get("label_width_mm", 0),
        requested_height_mm=profile.get("label_height_mm", 0),
        requested_qr_mm=profile.get("qr_size_mm", 0),
        qr_quiet_zone_modules=profile.get("qr_quiet_zone_modules", 0),
        qr_error_correction=profile.get("qr_error_correction", "M"),
        print_rotation_degrees=profile.get("print_rotation_degrees", 0),
        effective_width_mm=dots_to_mm(layout["effective_width_dots"]),
        effective_width_dots=layout["effective_width_dots"],
        width_warning=layout["width_warning"],
        preview_display_width_mm=preview_display_width_mm,
        preview_display_height_mm=preview_display_height_mm,
        ingress_base=ingress_base_path(),
        preview_query=preview_query_from_form(form, field_forms),
    )


def api_field_forms_from_payload(profile: Dict, payload: Dict) -> List[Dict]:
    values = payload.get("field_values") if isinstance(payload.get("field_values"), dict) else {}
    print_values = payload.get("print_fields") if isinstance(payload.get("print_fields"), dict) else {}
    field_list = payload.get("fields") if isinstance(payload.get("fields"), list) else []
    lookup = {}
    for item in field_list:
        if isinstance(item, dict):
            item_id = sanitize_id(str(item.get("id") or item.get("name") or ""), "")
            if item_id:
                lookup[item_id] = item
    forms: List[Dict] = []
    for field in profile.get("fields", []):
        current = dict(field)
        if field["id"] in values:
            current["value"] = str(values[field["id"]])
        elif field["id"] in lookup:
            current["value"] = str(lookup[field["id"]].get("value") or "")
        else:
            current["value"] = field["default_value"]
        if field["id"] in print_values:
            current["print_enabled"] = normalize_bool(print_values[field["id"]], field["print_by_default"])
        elif field["id"] in lookup and "print" in lookup[field["id"]]:
            current["print_enabled"] = normalize_bool(lookup[field["id"]].get("print"), field["print_by_default"])
        else:
            current["print_enabled"] = field["print_by_default"]
        forms.append(current)
    return forms


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
    opts = load_runtime_options()
    form, field_forms = form_data_from_request(opts)
    profile = opts.get("active_profile") or {}
    LOGGER.info("Opened UI for printer %s:%s", profile.get("printer_host"), profile.get("printer_port"))
    return render_page(form, opts, field_forms, result=None)


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_runtime_options()
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    result = {"success": False, "message": ui_text(opts, "unknown_error")}
    try:
        qr_value = validate_required_text(form.get("qr_value", ""), ui_text(opts, "qr_value_label"), opts["ui_language"])
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        copies = max(1, min(50, int(form.get("copies", "1"))))
        zpl = build_zpl(qr_value, field_forms, copies, profile)
        LOGGER.info("Print request received: profile=%s copies=%s qr_payload=%r", profile.get("id"), copies, qr_value)
        send_to_printer(profile["printer_host"], int(profile["printer_port"]), zpl)
        result = {"success": True, "message": ui_text(opts, "sent_labels_message", copies=copies, host=profile["printer_host"], port=profile["printer_port"], qr_payload=qr_value)}
    except Exception as exc:
        LOGGER.exception("Print failed")
        result = {"success": False, "message": ui_text(opts, "print_failed_message", error=exc)}
    return render_page(form, opts, field_forms, result=result)


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_runtime_options()
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    try:
        qr_value = validate_required_text(form.get("qr_value", ""), ui_text(opts, "qr_value_label"), opts["ui_language"])
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        copies = max(1, min(50, int(form.get("copies", "1"))))
        zpl = build_zpl(qr_value, field_forms, copies, profile)
        LOGGER.info("Generated ZPL preview for profile=%s copies=%s", profile.get("id"), copies)
        return Response(zpl, mimetype="text/plain; charset=utf-8")
    except Exception as exc:
        LOGGER.exception("ZPL preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/preview.png", methods=["GET"])
def preview_png():
    opts = load_runtime_options()
    form, field_forms = form_data_from_request(opts)
    try:
        qr_value = validate_required_text(form.get("qr_value", ""), ui_text(opts, "qr_value_label"), opts["ui_language"])
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        LOGGER.info("Generating PNG preview for profile=%s qr_value=%r", opts.get("active_profile_id"), qr_value)
        img = render_label_image(qr_value, field_forms, opts["active_profile"], preview=True)
        bio = BytesIO()
        img.save(bio, format="PNG", dpi=(203, 203), optimize=True)
        bio.seek(0)
        return send_file(bio, mimetype="image/png", download_name="label-preview.png")
    except Exception as exc:
        LOGGER.exception("PNG preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/api/print", methods=["POST"])
def api_print():
    payload = request.get_json(force=True, silent=False) or {}
    opts = load_runtime_options(str(payload.get("profile_id") or "") or None)
    profile = opts.get("active_profile") or {}
    try:
        qr_value = validate_required_text(payload.get("qr_value", profile.get("qr_default_value", "")), ui_text(opts, "qr_value_label"), opts["ui_language"])
        field_forms = validate_field_forms(api_field_forms_from_payload(profile, payload), opts["ui_language"])
        copies = max(1, min(50, int(payload.get("copies", 1))))
        zpl = build_zpl(qr_value, field_forms, copies, profile)
        LOGGER.info("API print request received: profile=%s copies=%s qr_value=%r", profile.get("id"), copies, qr_value)
        send_to_printer(profile["printer_host"], int(profile["printer_port"]), zpl)
        return jsonify({
            "ok": True,
            "profile_id": profile.get("id", ""),
            "printer": profile.get("printer_host", ""),
            "copies": copies,
            "qr_payload": qr_value,
            "language": opts["ui_language"],
        })
    except ValueError as exc:
        LOGGER.info("API print rejected: %s", exc)
        return jsonify({"ok": False, "error": str(exc), "language": opts["ui_language"]}), 400
    except Exception as exc:
        LOGGER.exception("API print failed")
        return jsonify({"ok": False, "error": str(exc), "language": opts["ui_language"]}), 500


if __name__ == "__main__":
    from waitress import serve
    serve(APP, host="0.0.0.0", port=8099)
