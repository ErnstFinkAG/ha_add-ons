import json
import logging
import math
import os
import re
import socket
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

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

LOGGER = logging.getLogger("inventory_label")
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
OPTION_MAX_LINES = {"sign_off": 2, "weight": 1}
ALIGNMENTS = {"left", "center", "right"}
FONT_FAMILIES = {"sans", "serif", "mono"}
SUPPORTED_UI_LANGUAGES = {"en", "de"}
SUPPORTED_ROTATIONS = {0, 90, 270}
FIELD_GAPS_MM = {1: 8.0, 2: 6.0, 3: 4.0}
FIELD_MAX_LINES = {1: 4, 2: 3, 3: 3}
FOOTER_MAX_LINES = 3
CUSTOM_BLOCK_MAX = 20
CUSTOM_BLOCK_MAX_LINES = 3
CUSTOM_BLOCK_DEFAULT_FONT_SIZE_MM = 7.0
CUSTOM_BLOCK_GAP_MM = 4.0

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
    "ui_language": "de",
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
    "field3_font_size_mm": 18.0,
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
    "footer_font_size_mm": 5.0,
    "footer_bottom_margin_mm": 0.0,
    "footer_bold": False,
    "footer_italic": False,
    "footer_underline": False,
    "sign_off_label": "Sign-off",
    "sign_off_default_value": "",
    "sign_off_options": "",
    "sign_off_alignment": "center",
    "sign_off_font_family": "sans",
    "sign_off_font_size_mm": 7.0,
    "sign_off_bold": False,
    "sign_off_italic": False,
    "sign_off_underline": False,
    "weight_label": "Weight (kg)",
    "weight_default_value": "",
    "weight_alignment": "center",
    "weight_font_family": "sans",
    "weight_font_size_mm": 7.0,
    "weight_bold": False,
    "weight_italic": False,
    "weight_underline": False,
    "qr_value_template": "{text1 - text2}",
    "qr_quiet_zone_modules": 3,
    "qr_error_correction": "M",
    "print_rotation_degrees": 0,
}

QR_ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

UI_STRINGS = {
    "en": {
        "lang": "en",
        "page_title": "Inventory Label",
        "intro_text": "Prints one large QR code label to a networked Zebra printer using raw ZPL over TCP. {field1_label} accepts digits only, and {weight_label} accepts digits only when enabled.",
        "print_field": "Print",
        "copies": "Copies",
        "configured_printer": "Configured printer",
        "print_label_button": "Print label",
        "preview_zpl": "Preview ZPL",
        "open_png_preview": "Open PNG preview",
        "preview_heading": "Preview",
        "preview_alt": "Label preview",
        "preview_meta": "PNG is rendered from the same layout coordinates used for print generation and exported at 203 dpi. The preview is shown at the configured label size in mm to approximate a 1:1 on-screen view. When print rotation is enabled, the preview keeps the same rotated aspect ratio as the printed label and, in horizontal mode, expands to the available preview width without horizontal scrolling. The red outline shows the full QR footprint including the configured quiet zone. Actual physical size can still vary with browser zoom, OS scaling, and display calibration.",
        "configured_label_mapping": "Configured label mapping",
        "field1_label_meta": "Field 1 label",
        "field2_label_meta": "Field 2 label",
        "field3_label_meta": "Field 3 label",
        "sign_off_label_meta": "Sign-off label",
        "configured_sign_off_names": "Configured sign-off names",
        "weight_label_meta": "Weight label",
        "footer_label_meta": "Footer label",
        "default_word": "default",
        "style_word": "style",
        "printed_as": "printed as",
        "qr_template": "QR template",
        "qr_quiet_zone": "QR quiet zone",
        "module_word": "module(s)",
        "qr_error_correction": "QR error correction",
        "footer_bottom_margin": "Footer bottom margin",
        "print_rotation": "Print rotation",
        "current_qr_payload": "Current QR payload",
        "current_print_selection": "Current print selection",
        "layout_heading": "Layout",
        "requested_label": "Requested label",
        "requested_qr": "Requested QR",
        "effective_print_width": "Effective print width on ZT420/ZT421 @ 203 dpi",
        "layout_note": "Field 1 is always printed in human-readable form. Fields 2, 3, weight, and the footer can be turned on or off in the UI for each label. Sign-off prints whenever it is filled in. Weight prints on its own line whenever it is enabled and filled in. When the footer is enabled, today's date is appended automatically at the end. The QR code follows the configured template above.",
        "on": "on",
        "off": "off",
        "none": "(none)",
        "configuration_error": "Configuration error: {error}",
        "unknown_error": "Unknown error",
        "sent_labels_message": "Sent {copies} label(s) to {host}:{port}. QR payload: {qr_payload}",
        "print_failed_message": "Print failed: {error}",
        "preview_failed_message": "Preview failed: {error}",
        "field_required": "{field} is required.",
        "field_numbers_only": "{field} must contain numbers only.",
        "custom_blocks_heading": "Custom text blocks",
        "add_custom_block_button": "Add custom block",
        "no_custom_blocks": "No custom text blocks added yet.",
        "custom_block_label": "Block label",
        "custom_block_value": "Block value",
        "custom_block_print": "Print this block",
        "custom_block_font_family": "Font family",
        "custom_block_font_size": "Font size (mm)",
        "custom_block_alignment": "Alignment",
        "custom_block_bold": "Bold",
        "custom_block_italic": "Italic",
        "custom_block_underline": "Underline",
        "custom_block_remove": "Remove",
        "custom_block_count": "Custom blocks",
        "custom_block_count_value": "{count} block(s)",
        "default_custom_block_label": "Custom",
        "font_family_sans": "Sans",
        "font_family_serif": "Serif",
        "font_family_mono": "Mono",
        "alignment_left": "Left",
        "alignment_center": "Center",
        "alignment_right": "Right",
    },
    "de": {
        "lang": "de",
        "page_title": "Inventaretikett",
        "intro_text": "Druckt ein großes QR-Code-Etikett auf einen Zebra-Netzwerkdrucker per rohem ZPL über TCP. {field1_label} akzeptiert nur Ziffern, und {weight_label} akzeptiert nur Ziffern, wenn das Feld aktiviert ist.",
        "print_field": "Drucken",
        "copies": "Exemplare",
        "configured_printer": "Konfigurierter Drucker",
        "print_label_button": "Etikett drucken",
        "preview_zpl": "ZPL-Vorschau",
        "open_png_preview": "PNG-Vorschau öffnen",
        "preview_heading": "Vorschau",
        "preview_alt": "Etikettenvorschau",
        "preview_meta": "Das PNG wird aus denselben Layout-Koordinaten wie der Druck erzeugt und mit 203 dpi exportiert. Die Vorschau wird in der konfigurierten Etikettengröße in mm angezeigt, um eine möglichst 1:1 Bildschirmdarstellung anzunähern. Wenn eine Druckdrehung aktiviert ist, behält auch die Vorschau das gedrehte Seitenverhältnis des gedruckten Etiketts und wird im horizontalen Modus auf die verfügbare Vorschaubreite eingepasst. Die rote Umrandung zeigt den gesamten QR-Bereich einschließlich der konfigurierten Ruhezone. Die tatsächliche physische Größe kann durch Browser-Zoom, OS-Skalierung und Bildschirmkalibrierung trotzdem abweichen.",
        "configured_label_mapping": "Konfigurierte Etikettenzuordnung",
        "field1_label_meta": "Feld-1-Bezeichnung",
        "field2_label_meta": "Feld-2-Bezeichnung",
        "field3_label_meta": "Feld-3-Bezeichnung",
        "sign_off_label_meta": "Signatur-Bezeichnung",
        "configured_sign_off_names": "Konfigurierte Signatur-Namen",
        "weight_label_meta": "Gewichtsbezeichnung",
        "footer_label_meta": "Fußzeilen-Bezeichnung",
        "default_word": "Standard",
        "style_word": "Stil",
        "printed_as": "gedruckt als",
        "qr_template": "QR-Vorlage",
        "qr_quiet_zone": "QR-Ruhezone",
        "module_word": "Modul(e)",
        "qr_error_correction": "QR-Fehlerkorrektur",
        "footer_bottom_margin": "Fußzeilen-Abstand unten",
        "print_rotation": "Drehung des Druckbilds",
        "current_qr_payload": "Aktueller QR-Inhalt",
        "current_print_selection": "Aktuelle Druckauswahl",
        "layout_heading": "Layout",
        "requested_label": "Angefordertes Etikett",
        "requested_qr": "Angeforderter QR-Code",
        "effective_print_width": "Effektive Druckbreite auf dem ZT420/ZT421 bei 203 dpi",
        "layout_note": "Feld 1 wird immer in Klarschrift gedruckt. Feld 2, Feld 3, Gewicht und die Fußzeile können pro Etikett in der Oberfläche ein- oder ausgeschaltet werden. Die Signatur wird gedruckt, sobald ein Wert eingetragen ist. Wenn Feld 3 und Gewicht beide aktiviert sind, wird das Gewicht mit <code> - </code> vor <code>kg</code> an Feld 3 angehängt. Wenn die Fußzeile aktiviert ist, wird das heutige Datum automatisch am Ende ergänzt. Der QR-Code folgt der oben konfigurierten Vorlage.",
        "on": "an",
        "off": "aus",
        "none": "(keine)",
        "configuration_error": "Konfigurationsfehler: {error}",
        "unknown_error": "Unbekannter Fehler",
        "sent_labels_message": "{copies} Etikett(en) an {host}:{port} gesendet. QR-Inhalt: {qr_payload}",
        "print_failed_message": "Drucken fehlgeschlagen: {error}",
        "preview_failed_message": "Vorschau fehlgeschlagen: {error}",
        "field_required": "{field} ist erforderlich.",
        "field_numbers_only": "{field} darf nur Ziffern enthalten.",
        "custom_blocks_heading": "Benutzerdefinierte Textblöcke",
        "add_custom_block_button": "Textblock hinzufügen",
        "no_custom_blocks": "Noch keine benutzerdefinierten Textblöcke hinzugefügt.",
        "custom_block_label": "Blockbezeichnung",
        "custom_block_value": "Blockinhalt",
        "custom_block_print": "Diesen Block drucken",
        "custom_block_font_family": "Schriftfamilie",
        "custom_block_font_size": "Schriftgröße (mm)",
        "custom_block_alignment": "Ausrichtung",
        "custom_block_bold": "Fett",
        "custom_block_italic": "Kursiv",
        "custom_block_underline": "Unterstrichen",
        "custom_block_remove": "Entfernen",
        "custom_block_count": "Benutzerdefinierte Blöcke",
        "custom_block_count_value": "{count} Block/Blöcke",
        "default_custom_block_label": "Benutzerdefiniert",
        "font_family_sans": "Sans",
        "font_family_serif": "Serif",
        "font_family_mono": "Mono",
        "alignment_left": "Links",
        "alignment_center": "Zentriert",
        "alignment_right": "Rechts",
    },
}

DEFAULT_FORM = {
    "copies": "1",
    "print_text2": "1",
    "print_text3": "1",
    "print_footer": "1",
    "print_weight": "0",
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
    input, select {
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
      min-width: 0;
      width: 100%;
    }
    .preview-frame {
      width: {{ preview_display_width_mm }}mm;
      height: {{ preview_display_height_mm }}mm;
      flex: 0 0 auto;
      max-width: none;
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
    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin: -6px 0 16px;
      font-weight: 500;
    }
    .checkbox-row input {
      width: auto;
      margin: 0;
      transform: scale(1.1);
      accent-color: var(--accent);
    }
    .section-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .small-button {
      padding: 10px 14px;
    }
    .small-muted {
      color: var(--muted);
      font-size: 0.92rem;
      margin-bottom: 12px;
    }
    .custom-blocks {
      display: grid;
      gap: 14px;
      margin-bottom: 16px;
    }
    .custom-block {
      background: #111827;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }
    .custom-block-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }
    .custom-block-title {
      font-weight: 700;
      color: var(--muted);
    }
    .custom-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
    }
    .inline-checks {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      margin-top: 8px;
    }
    .inline-checks label {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 0;
      font-weight: 500;
    }
    .inline-checks input {
      width: auto;
      margin: 0;
      accent-color: var(--accent);
    }
    .block-remove {
      background: transparent;
      border: 1px solid var(--danger);
      color: #fecaca;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{{ ui.page_title }}</h1>
      <p class="muted">{{ intro_text }}</p>
      {% if result %}
        <div class="flash {{ 'ok' if result.success else 'error' }}">{{ result.message }}</div>
      {% endif %}
      <form id="label-form" method="post" action="{{ ingress_base }}/print">
        <label for="text1">{{ field1_label }}</label>
        <input id="text1" name="text1" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="off" required value="{{ form.text1 }}">

        <label for="text2">{{ field2_label }}</label>
        <input id="text2" name="text2" value="{{ form.text2 }}">
        <label class="checkbox-row"><input id="print_text2" name="print_text2" type="checkbox" value="1" {% if form.print_text2 == '1' %}checked{% endif %}> {{ ui.print_field }} {{ field2_label }}</label>

        <label for="text3">{{ field3_label }}</label>
        <input id="text3" name="text3" value="{{ form.text3 }}">
        <label class="checkbox-row"><input id="print_text3" name="print_text3" type="checkbox" value="1" {% if form.print_text3 == '1' %}checked{% endif %}> {{ ui.print_field }} {{ field3_label }}</label>

        <label for="sign_off">{{ sign_off_label }}</label>
        <input id="sign_off" name="sign_off" list="signoff-options" value="{{ form.sign_off }}" autocomplete="off">
        <datalist id="signoff-options">
          {% for name in sign_off_options %}
            <option value="{{ name }}"></option>
          {% endfor %}
        </datalist>

        <label for="weight">{{ weight_label }}</label>
        <input id="weight" name="weight" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="off" value="{{ form.weight }}">
        <label class="checkbox-row"><input id="print_weight" name="print_weight" type="checkbox" value="1" {% if form.print_weight == '1' %}checked{% endif %}> {{ ui.print_field }} {{ weight_label }}</label>

        <label for="footer">{{ footer_label }}</label>
        <input id="footer" name="footer" value="{{ form.footer }}">
        <label class="checkbox-row"><input id="print_footer" name="print_footer" type="checkbox" value="1" {% if form.print_footer == '1' %}checked{% endif %}> {{ ui.print_field }} {{ footer_label }}</label>

        <input id="custom_blocks_json" name="custom_blocks_json" type="hidden" value="{{ custom_blocks_json|e }}">
        <div class="section-row">
          <label>{{ ui.custom_blocks_heading }}</label>
          <button id="add-custom-block" class="secondary small-button" type="button">{{ ui.add_custom_block_button }}</button>
        </div>
        <div id="custom-blocks-empty" class="small-muted">{{ ui.no_custom_blocks }}</div>
        <div id="custom-blocks-container" class="custom-blocks"></div>

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
          <a id="preview-zpl-link" class="button-link secondary" href="{{ ingress_base }}/preview?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&sign_off={{ form.sign_off|urlencode }}&weight={{ form.weight|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}&print_text2={{ form.print_text2 }}&print_text3={{ form.print_text3 }}&print_weight={{ form.print_weight }}&print_footer={{ form.print_footer }}&custom_blocks_json={{ custom_blocks_json|urlencode }}">{{ ui.preview_zpl }}</a>
          <a id="preview-png-link" class="button-link secondary" href="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&sign_off={{ form.sign_off|urlencode }}&weight={{ form.weight|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}&print_text2={{ form.print_text2 }}&print_text3={{ form.print_text3 }}&print_weight={{ form.print_weight }}&print_footer={{ form.print_footer }}&custom_blocks_json={{ custom_blocks_json|urlencode }}" target="_blank" rel="noopener">{{ ui.open_png_preview }}</a>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>{{ ui.preview_heading }}</h2>
      <div class="preview-wrap">
        <div class="preview-stage">
          <div class="preview-frame">
            <img id="preview-image" src="{{ ingress_base }}/preview.png?text1={{ form.text1|urlencode }}&text2={{ form.text2|urlencode }}&text3={{ form.text3|urlencode }}&sign_off={{ form.sign_off|urlencode }}&weight={{ form.weight|urlencode }}&footer={{ form.footer|urlencode }}&copies={{ form.copies }}&print_text2={{ form.print_text2 }}&print_text3={{ form.print_text3 }}&print_weight={{ form.print_weight }}&print_footer={{ form.print_footer }}&custom_blocks_json={{ custom_blocks_json|urlencode }}" alt="{{ ui.preview_alt }}">
          </div>
        </div>
      </div>
      <div class="preview-meta">
        {{ ui.preview_meta }}
      </div>
    </div>

    <div class="card">
      <h2>{{ ui.configured_label_mapping }}</h2>
      <ul class="config-list">
        <li><strong>{{ ui.field1_label_meta }}:</strong> <code>{{ field1_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ field1_default_value }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ field1_style_summary }}</code></li>
        <li><strong>{{ ui.field2_label_meta }}:</strong> <code>{{ field2_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ field2_default_value }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ field2_style_summary }}</code></li>
        <li><strong>{{ ui.field3_label_meta }}:</strong> <code>{{ field3_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ field3_default_value }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ field3_style_summary }}</code></li>
        <li><strong>{{ ui.sign_off_label_meta }}:</strong> <code>{{ sign_off_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ sign_off_default_value }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ sign_off_style_summary }}</code></li>
        <li><strong>{{ ui.configured_sign_off_names }}:</strong> <code>{{ sign_off_options_display }}</code></li>
        <li><strong>{{ ui.custom_block_count }}:</strong> <code>{{ custom_block_count_text }}</code></li>
        <li><strong>{{ ui.weight_label_meta }}:</strong> <code>{{ weight_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ weight_default_value }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ weight_style_summary }}</code></li>
        <li><strong>{{ ui.footer_label_meta }}:</strong> <code>{{ footer_label }}</code> · <strong>{{ ui.default_word }}:</strong> <code>{{ footer_default_value }}</code> · <strong>{{ ui.printed_as }}:</strong> <code>{{ footer_preview_text }}</code> · <strong>{{ ui.style_word }}:</strong> <code>{{ footer_style_summary }}</code></li>
        <li><strong>{{ ui.qr_template }}:</strong> <code>{{ qr_value_template }}</code></li>
        <li><strong>{{ ui.qr_quiet_zone }}:</strong> <code>{{ qr_quiet_zone_modules }} {{ ui.module_word }}</code></li>
        <li><strong>{{ ui.qr_error_correction }}:</strong> <code>{{ qr_error_correction }}</code></li>
        <li><strong>{{ ui.footer_bottom_margin }}:</strong> <code>{{ footer_bottom_margin_mm }} mm</code></li>
        <li><strong>{{ ui.current_qr_payload }}:</strong> <code>{{ qr_preview }}</code></li>
        <li><strong>{{ ui.current_print_selection }}:</strong> <code>field1={{ ui.on }}, field2={{ ui.on if form.print_text2 == "1" else ui.off }}, field3={{ ui.on if form.print_text3 == "1" else ui.off }}, sign-off={{ ui.on if form.sign_off else ui.off }}, weight={{ ui.on if form.print_weight == "1" else ui.off }}, footer={{ ui.on if form.print_footer == "1" else ui.off }}, custom={{ custom_block_count_text }}</code></li>
      </ul>
    </div>

    <div class="card">
      <h2>{{ ui.layout_heading }}</h2>
      <p class="muted">
        {{ ui.requested_label }}: {{ requested_width_mm }} × {{ requested_height_mm }} mm<br>
        {{ ui.requested_qr }}: {{ requested_qr_mm }} × {{ requested_qr_mm }} mm<br>
        QR quiet zone: {{ qr_quiet_zone_modules }} module(s), error correction: {{ qr_error_correction }}<br>
        Footer bottom margin: {{ footer_bottom_margin_mm }} mm<br>
        {{ ui.print_rotation }}: {{ print_rotation_degrees }}°<br>
        {{ ui.effective_print_width }}: {{ effective_width_mm }} mm ({{ effective_width_dots }} dots)
      </p>
      {% if width_warning %}
      <p class="warn">{{ width_warning }}</p>
      {% endif %}
      <p class="muted">{{ ui.layout_note|safe }}</p>
    </div>
  </div>
  <script>
    (function () {
      const ingressBase = {{ ingress_base|tojson }};
      const text1 = document.getElementById("text1");
      const text2 = document.getElementById("text2");
      const text3 = document.getElementById("text3");
      const signOff = document.getElementById("sign_off");
      const weight = document.getElementById("weight");
      const footer = document.getElementById("footer");
      const printText2 = document.getElementById("print_text2");
      const printText3 = document.getElementById("print_text3");
      const printWeight = document.getElementById("print_weight");
      const printFooter = document.getElementById("print_footer");
      const customBlocksJsonInput = document.getElementById("custom_blocks_json");
      const addCustomBlockButton = document.getElementById("add-custom-block");
      const customBlocksContainer = document.getElementById("custom-blocks-container");
      const customBlocksEmpty = document.getElementById("custom-blocks-empty");
      const copies = document.getElementById("copies");
      const previewImage = document.getElementById("preview-image");
      const previewFrame = document.querySelector(".preview-frame");
      const previewWrap = document.querySelector(".preview-wrap");
      const previewStage = document.querySelector(".preview-stage");
      const previewPngLink = document.getElementById("preview-png-link");
      const previewZplLink = document.getElementById("preview-zpl-link");
      if (!text1 || !text2 || !text3 || !signOff || !weight || !footer || !printText2 || !printText3 || !printWeight || !printFooter || !customBlocksJsonInput || !addCustomBlockButton || !customBlocksContainer || !customBlocksEmpty || !copies || !previewImage || !previewFrame || !previewWrap || !previewStage || !previewPngLink || !previewZplLink) return;

      const customBlockUi = {{ custom_block_ui|tojson }};
      const maxCustomBlocks = {{ custom_block_max }};
      const customBlockStorageKey = "inventory_label_custom_blocks";
      let refreshTimer = null;
      let previewNonce = Date.now();
      let customBlocks = [];

      function clampCustomBlockFontSize(value) {
        const parsed = parseFloat(value);
        if (Number.isNaN(parsed)) return 7;
        return Math.max(2, Math.min(30, parsed));
      }

      function normalizeCustomBlock(block, index) {
        const safe = block && typeof block === "object" ? block : {};
        const label = String(safe.label || `${customBlockUi.defaultLabel} ${index + 1}`).trim();
        const alignment = ["left", "center", "right"].includes(String(safe.alignment || "").toLowerCase()) ? String(safe.alignment).toLowerCase() : "center";
        const fontFamily = ["sans", "serif", "mono"].includes(String(safe.font_family || "").toLowerCase()) ? String(safe.font_family).toLowerCase() : "sans";
        return {
          label: label || `${customBlockUi.defaultLabel} ${index + 1}`,
          value: String(safe.value || ""),
          print: safe.print !== false,
          alignment,
          font_family: fontFamily,
          font_size_mm: clampCustomBlockFontSize(safe.font_size_mm),
          bold: Boolean(safe.bold),
          italic: Boolean(safe.italic),
          underline: Boolean(safe.underline),
        };
      }

      try {
        const parsed = JSON.parse(customBlocksJsonInput.value || "[]");
        customBlocks = Array.isArray(parsed) ? parsed.slice(0, maxCustomBlocks).map(normalizeCustomBlock) : [];
      } catch (error) {
        customBlocks = [];
      }

      if (!customBlocks.length) {
        try {
          const storedBlocks = window.localStorage.getItem(customBlockStorageKey);
          if (storedBlocks) {
            const parsedStored = JSON.parse(storedBlocks);
            customBlocks = Array.isArray(parsedStored) ? parsedStored.slice(0, maxCustomBlocks).map(normalizeCustomBlock) : [];
          }
        } catch (error) {
          customBlocks = [];
        }
      }

      function syncCustomBlocksField() {
        customBlocks = customBlocks.slice(0, maxCustomBlocks).map(normalizeCustomBlock);
        customBlocksJsonInput.value = JSON.stringify(customBlocks);
        try { window.localStorage.setItem(customBlockStorageKey, customBlocksJsonInput.value); } catch (error) {}
        customBlocksEmpty.style.display = customBlocks.length ? "none" : "block";
      }

      function makeOption(value, label, selected) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = selected;
        return option;
      }

      function appendField(wrapper, labelText, control) {
        const field = document.createElement("div");
        const label = document.createElement("label");
        label.textContent = labelText;
        field.appendChild(label);
        field.appendChild(control);
        wrapper.appendChild(field);
      }

      function renderCustomBlocks() {
        customBlocks = customBlocks.slice(0, maxCustomBlocks).map(normalizeCustomBlock);
        customBlocksContainer.innerHTML = "";
        customBlocks.forEach((block, index) => {
          const card = document.createElement("div");
          card.className = "custom-block";

          const header = document.createElement("div");
          header.className = "custom-block-header";
          const title = document.createElement("div");
          title.className = "custom-block-title";
          title.textContent = block.label || `${customBlockUi.defaultLabel} ${index + 1}`;
          const removeButton = document.createElement("button");
          removeButton.type = "button";
          removeButton.className = "secondary small-button block-remove";
          removeButton.textContent = customBlockUi.remove;
          removeButton.addEventListener("click", () => {
            customBlocks.splice(index, 1);
            renderCustomBlocks();
            applyPreviewUpdate();
          });
          header.appendChild(title);
          header.appendChild(removeButton);
          card.appendChild(header);

          const grid = document.createElement("div");
          grid.className = "custom-grid";

          const labelInput = document.createElement("input");
          labelInput.type = "text";
          labelInput.value = block.label;
          labelInput.addEventListener("input", () => {
            block.label = labelInput.value;
            syncCustomBlocksField();
            title.textContent = labelInput.value || `${customBlockUi.defaultLabel} ${index + 1}`;
            schedulePreviewUpdate();
          });
          appendField(grid, customBlockUi.label, labelInput);

          const valueInput = document.createElement("input");
          valueInput.type = "text";
          valueInput.value = block.value;
          valueInput.addEventListener("input", () => {
            block.value = valueInput.value;
            syncCustomBlocksField();
            schedulePreviewUpdate();
          });
          appendField(grid, customBlockUi.value, valueInput);

          const alignmentSelect = document.createElement("select");
          alignmentSelect.appendChild(makeOption("left", customBlockUi.alignmentLeft, block.alignment === "left"));
          alignmentSelect.appendChild(makeOption("center", customBlockUi.alignmentCenter, block.alignment === "center"));
          alignmentSelect.appendChild(makeOption("right", customBlockUi.alignmentRight, block.alignment === "right"));
          alignmentSelect.addEventListener("change", () => {
            block.alignment = alignmentSelect.value;
            syncCustomBlocksField();
            applyPreviewUpdate();
          });
          appendField(grid, customBlockUi.alignment, alignmentSelect);

          const familySelect = document.createElement("select");
          familySelect.appendChild(makeOption("sans", customBlockUi.fontSans, block.font_family === "sans"));
          familySelect.appendChild(makeOption("serif", customBlockUi.fontSerif, block.font_family === "serif"));
          familySelect.appendChild(makeOption("mono", customBlockUi.fontMono, block.font_family === "mono"));
          familySelect.addEventListener("change", () => {
            block.font_family = familySelect.value;
            syncCustomBlocksField();
            applyPreviewUpdate();
          });
          appendField(grid, customBlockUi.fontFamily, familySelect);

          const sizeInput = document.createElement("input");
          sizeInput.type = "number";
          sizeInput.min = "2";
          sizeInput.max = "30";
          sizeInput.step = "0.5";
          sizeInput.value = String(block.font_size_mm);
          sizeInput.addEventListener("input", () => {
            block.font_size_mm = clampCustomBlockFontSize(sizeInput.value);
            syncCustomBlocksField();
            schedulePreviewUpdate();
          });
          sizeInput.addEventListener("change", () => {
            sizeInput.value = String(clampCustomBlockFontSize(sizeInput.value));
            block.font_size_mm = clampCustomBlockFontSize(sizeInput.value);
            syncCustomBlocksField();
            applyPreviewUpdate();
          });
          appendField(grid, customBlockUi.fontSize, sizeInput);

          card.appendChild(grid);

          const checks = document.createElement("div");
          checks.className = "inline-checks";
          [
            ["print", customBlockUi.print],
            ["bold", customBlockUi.bold],
            ["italic", customBlockUi.italic],
            ["underline", customBlockUi.underline],
          ].forEach(([key, labelText]) => {
            const label = document.createElement("label");
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = Boolean(block[key]);
            checkbox.addEventListener("change", () => {
              block[key] = checkbox.checked;
              syncCustomBlocksField();
              applyPreviewUpdate();
            });
            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(labelText));
            checks.appendChild(label);
          });
          card.appendChild(checks);
          customBlocksContainer.appendChild(card);
        });
        syncCustomBlocksField();
      }

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
        params.set("sign_off", signOff.value || "");
        params.set("weight", weight.value || "");
        params.set("footer", footer.value || "");
        params.set("copies", normalizedCopies());
        params.set("print_text2", printText2.checked ? "1" : "0");
        params.set("print_text3", printText3.checked ? "1" : "0");
        params.set("print_weight", printWeight.checked ? "1" : "0");
        params.set("print_footer", printFooter.checked ? "1" : "0");
        params.set("custom_blocks_json", customBlocksJsonInput.value || "[]");
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
          previewFrame.style.width = `${Math.min({{ preview_display_width_mm }}, {{ preview_display_height_mm }})}mm`;
          previewFrame.style.height = `${Math.max({{ preview_display_width_mm }}, {{ preview_display_height_mm }})}mm`;
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

      const sanitizeText1 = () => {
        const digits = (text1.value || "").replace(/[^0-9]+/g, "");
        if (digits !== text1.value) text1.value = digits;
      };

      text1.addEventListener("input", () => {
        sanitizeText1();
        schedulePreviewUpdate();
      });
      text1.addEventListener("change", () => {
        sanitizeText1();
        applyPreviewUpdate();
      });

      const sanitizeWeight = () => {
        const digits = (weight.value || "").replace(/[^0-9]+/g, "");
        if (digits !== weight.value) weight.value = digits;
      };

      weight.addEventListener("input", () => {
        sanitizeWeight();
        schedulePreviewUpdate();
      });
      weight.addEventListener("change", () => {
        sanitizeWeight();
        applyPreviewUpdate();
      });

      [text2, text3, signOff, footer, copies].forEach((input) => {
        input.addEventListener("input", schedulePreviewUpdate);
        input.addEventListener("change", applyPreviewUpdate);
      });

      [printText2, printText3, printWeight, printFooter].forEach((checkbox) => {
        checkbox.addEventListener("input", schedulePreviewUpdate);
        checkbox.addEventListener("change", applyPreviewUpdate);
        checkbox.addEventListener("click", applyPreviewUpdate);
      });

      addCustomBlockButton.addEventListener("click", () => {
        if (customBlocks.length >= maxCustomBlocks) return;
        customBlocks.push(normalizeCustomBlock({ print: true }, customBlocks.length));
        renderCustomBlocks();
        applyPreviewUpdate();
      });

      previewImage.addEventListener("load", syncPreviewFrameToImage);
      window.addEventListener("resize", syncPreviewFrameToImage);

      sanitizeText1();
      sanitizeWeight();
      renderCustomBlocks();
      applyPreviewUpdate();
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


def normalize_ui_language(value: object, default: str) -> str:
    language = str(value if value is not None else default).strip().lower()
    return language if language in SUPPORTED_UI_LANGUAGES else default


def normalize_rotation_degrees(value: object, default: int) -> int:
    try:
        rotation = int(value)
    except (TypeError, ValueError):
        rotation = default
    return rotation if rotation in SUPPORTED_ROTATIONS else default


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
    options["print_rotation_degrees"] = normalize_rotation_degrees(options.get("print_rotation_degrees"), DEFAULT_OPTIONS["print_rotation_degrees"])

    for idx in range(1, FIELD_COUNT + 1):
        options[f"field{idx}_label"] = normalize_string(options.get(f"field{idx}_label"), DEFAULT_OPTIONS[f"field{idx}_label"])
        options[f"field{idx}_default_value"] = "" if options.get(f"field{idx}_default_value") is None else str(options.get(f"field{idx}_default_value"))
        options[f"field{idx}_alignment"] = normalize_alignment(options.get(f"field{idx}_alignment"), DEFAULT_OPTIONS[f"field{idx}_alignment"])
        options[f"field{idx}_font_family"] = normalize_font_family(options.get(f"field{idx}_font_family"), DEFAULT_OPTIONS[f"field{idx}_font_family"])
        options[f"field{idx}_font_size_mm"] = normalize_float(options.get(f"field{idx}_font_size_mm"), DEFAULT_OPTIONS[f"field{idx}_font_size_mm"], 2.0, 30.0)
        options[f"field{idx}_bold"] = normalize_bool(options.get(f"field{idx}_bold"), DEFAULT_OPTIONS[f"field{idx}_bold"])
        options[f"field{idx}_italic"] = normalize_bool(options.get(f"field{idx}_italic"), DEFAULT_OPTIONS[f"field{idx}_italic"])
        options[f"field{idx}_underline"] = normalize_bool(options.get(f"field{idx}_underline"), DEFAULT_OPTIONS[f"field{idx}_underline"])
    options["footer_label"] = normalize_string(options.get("footer_label"), DEFAULT_OPTIONS["footer_label"])
    options["footer_default_value"] = "" if options.get("footer_default_value") is None else str(options.get("footer_default_value"))
    options["footer_alignment"] = normalize_alignment(options.get("footer_alignment"), DEFAULT_OPTIONS["footer_alignment"])
    options["footer_font_family"] = normalize_font_family(options.get("footer_font_family"), DEFAULT_OPTIONS["footer_font_family"])
    options["footer_font_size_mm"] = normalize_float(options.get("footer_font_size_mm"), DEFAULT_OPTIONS["footer_font_size_mm"], 2.0, 30.0)
    options["footer_bottom_margin_mm"] = normalize_float(options.get("footer_bottom_margin_mm"), DEFAULT_OPTIONS["footer_bottom_margin_mm"], 0.0, 100.0)
    options["footer_bold"] = normalize_bool(options.get("footer_bold"), DEFAULT_OPTIONS["footer_bold"])
    options["footer_italic"] = normalize_bool(options.get("footer_italic"), DEFAULT_OPTIONS["footer_italic"])
    options["footer_underline"] = normalize_bool(options.get("footer_underline"), DEFAULT_OPTIONS["footer_underline"])
    options["sign_off_label"] = normalize_string(options.get("sign_off_label"), DEFAULT_OPTIONS["sign_off_label"])
    options["sign_off_default_value"] = "" if options.get("sign_off_default_value") is None else str(options.get("sign_off_default_value"))
    options["sign_off_options"] = str(options.get("sign_off_options") or DEFAULT_OPTIONS["sign_off_options"])
    options["sign_off_alignment"] = normalize_alignment(options.get("sign_off_alignment"), DEFAULT_OPTIONS["sign_off_alignment"])
    options["sign_off_font_family"] = normalize_font_family(options.get("sign_off_font_family"), DEFAULT_OPTIONS["sign_off_font_family"])
    options["sign_off_font_size_mm"] = normalize_float(options.get("sign_off_font_size_mm"), DEFAULT_OPTIONS["sign_off_font_size_mm"], 2.0, 30.0)
    options["sign_off_bold"] = normalize_bool(options.get("sign_off_bold"), DEFAULT_OPTIONS["sign_off_bold"])
    options["sign_off_italic"] = normalize_bool(options.get("sign_off_italic"), DEFAULT_OPTIONS["sign_off_italic"])
    options["sign_off_underline"] = normalize_bool(options.get("sign_off_underline"), DEFAULT_OPTIONS["sign_off_underline"])
    options["weight_label"] = normalize_string(options.get("weight_label"), DEFAULT_OPTIONS["weight_label"])
    options["weight_default_value"] = digits_only("" if options.get("weight_default_value") is None else options.get("weight_default_value"))
    options["weight_alignment"] = normalize_alignment(options.get("weight_alignment"), DEFAULT_OPTIONS["weight_alignment"])
    options["weight_font_family"] = normalize_font_family(options.get("weight_font_family"), DEFAULT_OPTIONS["weight_font_family"])
    options["weight_font_size_mm"] = normalize_float(options.get("weight_font_size_mm"), DEFAULT_OPTIONS["weight_font_size_mm"], 2.0, 30.0)
    options["weight_bold"] = normalize_bool(options.get("weight_bold"), DEFAULT_OPTIONS["weight_bold"])
    options["weight_italic"] = normalize_bool(options.get("weight_italic"), DEFAULT_OPTIONS["weight_italic"])
    options["weight_underline"] = normalize_bool(options.get("weight_underline"), DEFAULT_OPTIONS["weight_underline"])
    return options


def default_form_from_options(opts: Dict) -> Dict[str, str]:
    return {
        "text1": digits_only(opts.get("field1_default_value") or ""),
        "text2": str(opts.get("field2_default_value") or ""),
        "text3": str(opts.get("field3_default_value") or ""),
        "sign_off": str(opts.get("sign_off_default_value") or ""),
        "weight": digits_only(opts.get("weight_default_value") or ""),
        "footer": str(opts.get("footer_default_value") or ""),
        "custom_blocks_json": "[]",
        "copies": DEFAULT_FORM["copies"],
        "print_text2": DEFAULT_FORM["print_text2"],
        "print_text3": DEFAULT_FORM["print_text3"],
        "print_weight": DEFAULT_FORM["print_weight"],
        "print_footer": DEFAULT_FORM["print_footer"],
    }


def form_data_from_request(opts: Dict) -> Dict[str, str]:
    defaults = default_form_from_options(opts)
    return {
        "text1": digits_only(request.values.get("text1", defaults["text1"]), defaults["text1"]),
        "text2": request.values.get("text2", defaults["text2"]),
        "text3": request.values.get("text3", defaults["text3"]),
        "sign_off": request.values.get("sign_off", defaults["sign_off"]),
        "weight": digits_only(request.values.get("weight", defaults["weight"]), defaults["weight"]),
        "footer": request.values.get("footer", defaults["footer"]),
        "custom_blocks_json": request.values.get("custom_blocks_json", defaults["custom_blocks_json"]),
        "copies": request.values.get("copies", defaults["copies"]),
        "print_text2": normalize_form_checkbox(request.values.get("print_text2"), defaults["print_text2"]),
        "print_text3": normalize_form_checkbox(request.values.get("print_text3"), defaults["print_text3"]),
        "print_weight": normalize_form_checkbox(request.values.get("print_weight"), defaults["print_weight"]),
        "print_footer": normalize_form_checkbox(request.values.get("print_footer"), defaults["print_footer"]),
    }


def normalize_form_checkbox(value: object, default: str = "0") -> str:
    if value is None:
        return default
    return "1" if normalize_bool(value, False) else "0"


def digits_only(value: object, fallback: str = "") -> str:
    text = fallback if value is None else str(value)
    return re.sub(r"\D+", "", text)


def validate_text1_numeric(value: object, field1_label: str, language: str = "en") -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(ui_text(language, "field_required", field=field1_label))
    if not text.isdigit():
        raise ValueError(ui_text(language, "field_numbers_only", field=field1_label))
    return text


def validate_optional_numeric(value: object, label: str, language: str = "en") -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if not text.isdigit():
        raise ValueError(ui_text(language, "field_numbers_only", field=label))
    return text


def parse_print_toggles(data: Dict[str, str]) -> Dict[str, bool]:
    return {
        "print_text2": normalize_bool(data.get("print_text2"), True),
        "print_text3": normalize_bool(data.get("print_text3"), True),
        "print_weight": normalize_bool(data.get("print_weight"), False),
        "print_footer": normalize_bool(data.get("print_footer"), True),
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
    return f"{style_summary_from_prefix(opts, 'footer')}, bottom margin {opts['footer_bottom_margin_mm']} mm"


def option_style_summary(opts: Dict, name: str) -> str:
    return style_summary_from_prefix(opts, name)


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
        "bottom_margin_mm": opts["footer_bottom_margin_mm"],
        "bold": opts["footer_bold"],
        "italic": opts["footer_italic"],
        "underline": opts["footer_underline"],
        "max_lines": FOOTER_MAX_LINES,
        "gap_after_dots": 0,
    }


def get_optional_block_config(opts: Dict, name: str) -> Dict:
    return {
        "label": opts[f"{name}_label"],
        "default_value": opts[f"{name}_default_value"],
        "alignment": opts[f"{name}_alignment"],
        "font_family": opts[f"{name}_font_family"],
        "font_size_mm": opts[f"{name}_font_size_mm"],
        "bold": opts[f"{name}_bold"],
        "italic": opts[f"{name}_italic"],
        "underline": opts[f"{name}_underline"],
        "max_lines": OPTION_MAX_LINES.get(name, 2),
        "gap_after_dots": mm_to_dots(4.0 if name == "sign_off" else 6.0),
    }


def get_custom_block_config(block: Dict) -> Dict:
    return {
        "label": str(block.get("label") or ""),
        "default_value": str(block.get("value") or ""),
        "alignment": normalize_alignment(block.get("alignment"), "center"),
        "font_family": normalize_font_family(block.get("font_family"), "sans"),
        "font_size_mm": normalize_float(block.get("font_size_mm"), CUSTOM_BLOCK_DEFAULT_FONT_SIZE_MM, 2.0, 30.0),
        "bold": normalize_bool(block.get("bold"), False),
        "italic": normalize_bool(block.get("italic"), False),
        "underline": normalize_bool(block.get("underline"), False),
        "max_lines": CUSTOM_BLOCK_MAX_LINES,
        "gap_after_dots": mm_to_dots(CUSTOM_BLOCK_GAP_MM),
    }


def parse_sign_off_options(opts: Dict) -> List[str]:
    raw = str(opts.get("sign_off_options") or "")
    parts = [part.strip() for part in re.split(r"\n|,|;", raw)]
    seen = set()
    names: List[str] = []
    for part in parts:
        if part and part.lower() not in seen:
            seen.add(part.lower())
            names.append(part)
    return names


def normalize_custom_block_entry(raw: object, idx: int = 0) -> Dict:
    data = raw if isinstance(raw, dict) else {}
    return {
        "label": str(data.get("label") or f"Custom {idx + 1}").strip() or f"Custom {idx + 1}",
        "value": str(data.get("value") or ""),
        "print": normalize_bool(data.get("print"), True),
        "alignment": normalize_alignment(data.get("alignment"), "center"),
        "font_family": normalize_font_family(data.get("font_family"), "sans"),
        "font_size_mm": normalize_float(data.get("font_size_mm"), CUSTOM_BLOCK_DEFAULT_FONT_SIZE_MM, 2.0, 30.0),
        "bold": normalize_bool(data.get("bold"), False),
        "italic": normalize_bool(data.get("italic"), False),
        "underline": normalize_bool(data.get("underline"), False),
    }


def parse_custom_blocks(raw: object) -> List[Dict]:
    if raw in (None, "", []):
        return []
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            LOGGER.warning("Failed to parse custom block JSON; ignoring value")
            return []
    if not isinstance(data, list):
        return []
    blocks: List[Dict] = []
    for idx, item in enumerate(data[:CUSTOM_BLOCK_MAX]):
        blocks.append(normalize_custom_block_entry(item, idx))
    return blocks


def custom_blocks_json_value(blocks: List[Dict]) -> str:
    return json.dumps(blocks or [], ensure_ascii=False, separators=(",", ":"))


def current_label_date_str() -> str:
    tz_name = os.environ.get("TZ") or "Europe/Zurich"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
    return now.strftime("%d.%m.%Y")


def compose_footer_text(footer: str) -> str:
    footer_text = (footer or "").strip()
    if not footer_text:
        return ""
    return f"{footer_text} - {current_label_date_str()}"

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


def render_custom_blocks_sequence(draw: ImageDraw.ImageDraw, current_y: int, box_left: int, box_width: int, custom_blocks: List[Dict]) -> int:
    for block in custom_blocks or []:
        if not normalize_bool(block.get("print"), True):
            continue
        block_value = str(block.get("value") or "").strip()
        if not block_value:
            continue
        cfg = get_custom_block_config(block)
        font, lines, resolved_font_size = fit_field_lines(draw, block_value, cfg, box_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            box_left,
            box_width,
            font,
            cfg["alignment"],
            cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += cfg["gap_after_dots"]
    return current_y


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


def render_portrait_content(printable_w: int, canvas_h: int, text1: str, text2: str, text3: str, sign_off: str, weight: str, footer: str, custom_blocks: List[Dict], opts: Dict, preview: bool, print_toggles: Dict[str, bool]) -> Image.Image:
    layout = effective_layout(opts)
    qr_payload = build_qr_payload(text1, text2, text3, opts)
    img = Image.new("RGB", (printable_w, canvas_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    qr_size = min(layout["qr_size_dots"], printable_w)
    qr_left = max((printable_w - qr_size) // 2, 0)
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

    margin_x = max((printable_w - qr_size) // 2, mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM))
    text_width = max(1, printable_w - (margin_x * 2))
    current_y = qr_top + qr_size + mm_to_dots(8)

    weight_text = (weight or "").strip()

    text_values = {1: text1, 2: text2, 3: text3}
    field_enabled = {1: True, 2: print_toggles.get("print_text2", True), 3: print_toggles.get("print_text3", True)}
    for idx in range(1, FIELD_COUNT + 1):
        if not field_enabled.get(idx, True):
            continue

        field_value = text_values[idx]

        cfg = get_field_config(opts, idx)
        font, lines, resolved_font_size = fit_field_lines(draw, field_value, cfg, text_width)
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

    sign_off_text = (sign_off or "").strip()
    if sign_off_text:
        sign_off_cfg = get_optional_block_config(opts, "sign_off")
        font, lines, resolved_font_size = fit_field_lines(draw, sign_off_text, sign_off_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            margin_x,
            text_width,
            font,
            sign_off_cfg["alignment"],
            sign_off_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += sign_off_cfg["gap_after_dots"]

    if weight_text and print_toggles.get("print_weight", False):
        weight_cfg = get_optional_block_config(opts, "weight")
        font, lines, resolved_font_size = fit_field_lines(draw, f"{weight_text} kg", weight_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            margin_x,
            text_width,
            font,
            weight_cfg["alignment"],
            weight_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += weight_cfg["gap_after_dots"]

    current_y = render_custom_blocks_sequence(draw, current_y, margin_x, text_width, custom_blocks)

    footer_text = compose_footer_text(footer)
    if footer_text and print_toggles.get("print_footer", True):
        footer_cfg = get_footer_config(opts)
        font, lines, resolved_font_size = fit_field_lines(draw, footer_text, footer_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        footer_height = text_block_height(draw, font, len(lines), line_spacing)
        footer_bottom_margin_dots = mm_to_dots(footer_cfg.get("bottom_margin_mm", 0.0)) if footer_cfg.get("bottom_margin_mm", 0.0) > 0 else 0
        footer_y = max(0, canvas_h - footer_height - footer_bottom_margin_dots)
        if footer_y < current_y:
            LOGGER.warning(
                "Footer overlaps content: footer_y=%s current_y=%s footer_height=%s label_height=%s",
                footer_y,
                current_y,
                footer_height,
                canvas_h,
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


def render_rotated_content(printable_w: int, canvas_h: int, text1: str, text2: str, text3: str, sign_off: str, weight: str, footer: str, custom_blocks: List[Dict], opts: Dict, preview: bool, print_toggles: Dict[str, bool], rotation_degrees: int) -> Image.Image:
    layout = effective_layout(opts)
    qr_payload = build_qr_payload(text1, text2, text3, opts)
    logical_w = canvas_h
    logical_h = printable_w
    landscape = Image.new("RGB", (logical_w, logical_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(landscape)

    qr_size = min(layout["qr_size_dots"], logical_h)
    qr_left = min(max(layout["top_margin_dots"], 0), max(0, logical_w - qr_size))
    qr_top = max((logical_h - qr_size) // 2, 0)
    qr_img = build_qr_image(qr_payload, qr_size, opts).convert("RGB")
    landscape.paste(qr_img, (qr_left, qr_top))

    if preview:
        preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
        draw.rectangle(
            (qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1),
            outline=(220, 38, 38),
            width=preview_border_width,
        )

    inter_block_gap = mm_to_dots(8)
    left_margin = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    right_margin = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    text_left = min(logical_w, qr_left + qr_size + inter_block_gap)
    text_right_margin = right_margin
    text_width = max(1, logical_w - text_left - text_right_margin)
    text_box_top = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    text_box_bottom = logical_h - mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    current_y = text_box_top

    weight_text = (weight or "").strip()

    text_values = {1: text1, 2: text2, 3: text3}
    field_enabled = {1: True, 2: print_toggles.get("print_text2", True), 3: print_toggles.get("print_text3", True)}
    for idx in range(1, FIELD_COUNT + 1):
        if not field_enabled.get(idx, True):
            continue
        field_value = text_values[idx]

        cfg = get_field_config(opts, idx)
        font, lines, resolved_font_size = fit_field_lines(draw, field_value, cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            text_left,
            text_width,
            font,
            cfg["alignment"],
            cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += cfg["gap_after_dots"]

    sign_off_text = (sign_off or "").strip()
    if sign_off_text:
        sign_off_cfg = get_optional_block_config(opts, "sign_off")
        font, lines, resolved_font_size = fit_field_lines(draw, sign_off_text, sign_off_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            text_left,
            text_width,
            font,
            sign_off_cfg["alignment"],
            sign_off_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += sign_off_cfg["gap_after_dots"]

    if weight_text and print_toggles.get("print_weight", False):
        weight_cfg = get_optional_block_config(opts, "weight")
        font, lines, resolved_font_size = fit_field_lines(draw, f"{weight_text} kg", weight_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        current_y = draw_aligned_text_lines(
            draw,
            lines,
            current_y,
            text_left,
            text_width,
            font,
            weight_cfg["alignment"],
            weight_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )
        current_y += weight_cfg["gap_after_dots"]

    current_y = render_custom_blocks_sequence(draw, current_y, text_left, text_width, custom_blocks)

    footer_text = compose_footer_text(footer)
    if footer_text and print_toggles.get("print_footer", True):
        footer_cfg = get_footer_config(opts)
        font, lines, resolved_font_size = fit_field_lines(draw, footer_text, footer_cfg, text_width)
        line_spacing = max(4, resolved_font_size // 7)
        footer_height = text_block_height(draw, font, len(lines), line_spacing)
        footer_bottom_margin_dots = mm_to_dots(footer_cfg.get("bottom_margin_mm", 0.0)) if footer_cfg.get("bottom_margin_mm", 0.0) > 0 else 0
        footer_y = max(text_box_top, text_box_bottom - footer_height - footer_bottom_margin_dots)
        if footer_y < current_y:
            LOGGER.warning(
                "Footer overlaps rotated content: footer_y=%s current_y=%s footer_height=%s logical_height=%s",
                footer_y,
                current_y,
                footer_height,
                logical_h,
            )
        draw_aligned_text_lines(
            draw,
            lines,
            footer_y,
            text_left,
            text_width,
            font,
            footer_cfg["alignment"],
            footer_cfg["underline"],
            fill=(0, 0, 0),
            line_spacing=line_spacing,
        )

    if rotation_degrees == 90:
        return landscape.transpose(Image.Transpose.ROTATE_270)
    return landscape.transpose(Image.Transpose.ROTATE_90)


def orient_preview_for_display(img: Image.Image, rotation_degrees: int) -> Image.Image:
    if rotation_degrees == 90:
        return img.transpose(Image.Transpose.ROTATE_90)
    if rotation_degrees == 270:
        return img.transpose(Image.Transpose.ROTATE_270)
    return img


def render_label_image(text1: str, text2: str, text3: str, sign_off: str, weight: str, footer: str, custom_blocks: List[Dict], opts: Dict, preview: bool, print_toggles: Dict[str, bool] | None = None) -> Image.Image:
    layout = effective_layout(opts)
    requested_w = layout["requested_width_dots"]
    requested_h = layout["requested_height_dots"]
    printable_w = layout["effective_width_dots"]
    rotation_degrees = normalize_rotation_degrees(opts.get("print_rotation_degrees"), DEFAULT_OPTIONS["print_rotation_degrees"])
    print_toggles = print_toggles or {"print_text2": True, "print_text3": True, "print_weight": False, "print_footer": True}

    printable_image = (
        render_portrait_content(printable_w, requested_h, text1, text2, text3, sign_off, weight, footer, custom_blocks, opts, preview, print_toggles)
        if rotation_degrees == 0
        else render_rotated_content(printable_w, requested_h, text1, text2, text3, sign_off, weight, footer, custom_blocks, opts, preview, print_toggles, rotation_degrees)
    )

    if not preview:
        return printable_image

    if requested_w <= printable_w:
        return orient_preview_for_display(printable_image, rotation_degrees)

    canvas = Image.new("RGB", (requested_w, requested_h), color=(255, 255, 255))
    printable_left = max((requested_w - printable_w) // 2, 0)
    draw_background_for_preview(canvas, requested_w, requested_h, printable_left, printable_w)
    canvas.paste(printable_image, (printable_left, 0))
    return orient_preview_for_display(canvas, rotation_degrees)


def build_zpl(text1: str, text2: str, text3: str, sign_off: str, weight: str, footer: str, custom_blocks: List[Dict], copies: int, opts: Dict, print_toggles: Dict[str, bool] | None = None) -> str:
    layout = effective_layout(opts)
    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    label_img = render_label_image(text1, text2, text3, sign_off, weight, footer, custom_blocks, opts, preview=False, print_toggles=print_toggles).convert("1")
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
    sign_off_names = parse_sign_off_options(opts)
    custom_blocks = parse_custom_blocks(form.get("custom_blocks_json", "[]"))
    custom_blocks_json = custom_blocks_json_value(custom_blocks)
    ui = get_ui_strings(opts.get("ui_language"))
    try:
        qr_preview = build_qr_payload(form.get("text1", ""), form.get("text2", ""), form.get("text3", ""), opts)
    except Exception as exc:
        qr_preview = ui_text(opts, "configuration_error", error=exc)

    preview_display_width_mm = opts["label_width_mm"]
    preview_display_height_mm = opts["label_height_mm"]
    if normalize_rotation_degrees(opts.get("print_rotation_degrees"), DEFAULT_OPTIONS["print_rotation_degrees"]) in (90, 270):
        preview_display_width_mm, preview_display_height_mm = preview_display_height_mm, preview_display_width_mm

    return render_template_string(
        HTML,
        ui=ui,
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
        sign_off_label=opts["sign_off_label"],
        sign_off_default_value=opts["sign_off_default_value"],
        sign_off_options=sign_off_names,
        sign_off_options_display=", ".join(sign_off_names) if sign_off_names else ui["none"],
        custom_blocks_json=custom_blocks_json,
        custom_block_count_text=ui_text(opts, "custom_block_count_value", count=len(custom_blocks)),
        custom_block_ui={
            "label": ui["custom_block_label"],
            "value": ui["custom_block_value"],
            "print": ui["custom_block_print"],
            "fontFamily": ui["custom_block_font_family"],
            "fontSize": ui["custom_block_font_size"],
            "alignment": ui["custom_block_alignment"],
            "bold": ui["custom_block_bold"],
            "italic": ui["custom_block_italic"],
            "underline": ui["custom_block_underline"],
            "remove": ui["custom_block_remove"],
            "defaultLabel": ui["default_custom_block_label"],
            "fontSans": ui["font_family_sans"],
            "fontSerif": ui["font_family_serif"],
            "fontMono": ui["font_family_mono"],
            "alignmentLeft": ui["alignment_left"],
            "alignmentCenter": ui["alignment_center"],
            "alignmentRight": ui["alignment_right"],
        },
        custom_block_max=CUSTOM_BLOCK_MAX,
        intro_text=ui_text(opts, "intro_text", field1_label=opts["field1_label"], weight_label=opts["weight_label"]),
        sign_off_style_summary=option_style_summary(opts, "sign_off"),
        weight_label=opts["weight_label"],
        weight_default_value=opts["weight_default_value"],
        weight_style_summary=option_style_summary(opts, "weight"),
        footer_label=opts["footer_label"],
        footer_default_value=opts["footer_default_value"],
        footer_preview_text=compose_footer_text(form.get("footer", opts["footer_default_value"])),
        footer_style_summary=footer_style_summary(opts),
        qr_value_template=opts["qr_value_template"],
        qr_quiet_zone_modules=opts["qr_quiet_zone_modules"],
        qr_error_correction=opts["qr_error_correction"],
        qr_preview=qr_preview,
        requested_width_mm=opts["label_width_mm"],
        requested_height_mm=opts["label_height_mm"],
        preview_display_width_mm=preview_display_width_mm,
        preview_display_height_mm=preview_display_height_mm,
        requested_qr_mm=opts["qr_size_mm"],
        effective_width_mm=dots_to_mm(layout["effective_width_dots"]),
        effective_width_dots=layout["effective_width_dots"],
        width_warning=layout["width_warning"],
        footer_bottom_margin_mm=opts["footer_bottom_margin_mm"],
        print_rotation_degrees=opts["print_rotation_degrees"],
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
    form = {
        "text1": digits_only(request.form.get("text1", defaults["text1"]), defaults["text1"]),
        "text2": request.form.get("text2", defaults["text2"]).strip(),
        "text3": request.form.get("text3", defaults["text3"]).strip(),
        "sign_off": request.form.get("sign_off", defaults["sign_off"]).strip(),
        "weight": digits_only(request.form.get("weight", defaults["weight"]), defaults["weight"]),
        "footer": request.form.get("footer", defaults["footer"]).strip(),
        "custom_blocks_json": request.form.get("custom_blocks_json", defaults["custom_blocks_json"]),
        "copies": request.form.get("copies", DEFAULT_FORM["copies"]).strip() or DEFAULT_FORM["copies"],
        "print_text2": normalize_form_checkbox(request.form.get("print_text2"), defaults["print_text2"]),
        "print_text3": normalize_form_checkbox(request.form.get("print_text3"), defaults["print_text3"]),
        "print_weight": normalize_form_checkbox(request.form.get("print_weight"), defaults["print_weight"]),
        "print_footer": normalize_form_checkbox(request.form.get("print_footer"), defaults["print_footer"]),
    }

    result = {"success": False, "message": ui_text(opts, "unknown_error")}
    try:
        raw_text1 = request.form.get("text1", defaults["text1"])
        text1 = validate_text1_numeric(raw_text1, opts["field1_label"], opts["ui_language"])
        form["text1"] = text1
        text2 = form["text2"]
        text3 = form["text3"]
        sign_off = form["sign_off"]
        weight = validate_optional_numeric(form["weight"], opts["weight_label"], opts["ui_language"])
        form["weight"] = weight
        footer = form["footer"]
        custom_blocks = parse_custom_blocks(form.get("custom_blocks_json", "[]"))
        form["custom_blocks_json"] = custom_blocks_json_value(custom_blocks)
        copies = max(1, min(50, int(form["copies"])))
        print_toggles = parse_print_toggles(form)
        zpl = build_zpl(text1, text2, text3, sign_off, weight, footer, custom_blocks, copies, opts, print_toggles=print_toggles)
        qr_payload = build_qr_payload(text1, text2, text3, opts)
        LOGGER.info(
            "Print request received: copies=%s qr_payload=%r sign_off=%r weight=%r custom_blocks=%s print_text2=%s print_text3=%s print_weight=%s print_footer=%s",
            copies,
            qr_payload,
            sign_off,
            weight,
            len(custom_blocks),
            print_toggles["print_text2"],
            print_toggles["print_text3"],
            print_toggles["print_weight"],
            print_toggles["print_footer"],
        )
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        result = {
            "success": True,
            "message": ui_text(opts, "sent_labels_message", copies=copies, host=opts["printer_host"], port=opts["printer_port"], qr_payload=qr_payload),
        }
    except Exception as exc:
        LOGGER.exception("Print failed")
        result = {"success": False, "message": ui_text(opts, "print_failed_message", error=exc)}

    return render_page(form, opts, result=result)


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_options()
    defaults = default_form_from_options(opts)
    raw_text1 = request.args.get("text1", defaults["text1"])
    text2 = request.args.get("text2", defaults["text2"])
    text3 = request.args.get("text3", defaults["text3"])
    sign_off = request.args.get("sign_off", defaults["sign_off"])
    weight = request.args.get("weight", defaults["weight"])
    footer = request.args.get("footer", defaults["footer"])
    custom_blocks = parse_custom_blocks(request.args.get("custom_blocks_json", defaults["custom_blocks_json"]))
    try:
        text1 = validate_text1_numeric(raw_text1, opts["field1_label"], opts["ui_language"])
        copies = max(1, min(50, int(request.args.get("copies", DEFAULT_FORM["copies"]))))
        print_toggles = parse_print_toggles({
            "print_text2": request.args.get("print_text2", defaults["print_text2"]),
            "print_text3": request.args.get("print_text3", defaults["print_text3"]),
            "print_weight": request.args.get("print_weight", defaults["print_weight"]),
            "print_footer": request.args.get("print_footer", defaults["print_footer"]),
        })
        weight = validate_optional_numeric(weight, opts["weight_label"], opts["ui_language"])
        zpl = build_zpl(text1, text2, text3, sign_off, weight, footer, custom_blocks, copies, opts, print_toggles=print_toggles)
        LOGGER.info("Generated ZPL preview for copies=%s with toggles=%s and custom_blocks=%s", copies, print_toggles, len(custom_blocks))
        return Response(zpl, mimetype="text/plain; charset=utf-8")
    except Exception as exc:
        LOGGER.exception("ZPL preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/preview.png", methods=["GET"])
def preview_png():
    opts = load_options()
    defaults = default_form_from_options(opts)
    raw_text1 = request.args.get("text1", defaults["text1"])
    text2 = request.args.get("text2", defaults["text2"])
    text3 = request.args.get("text3", defaults["text3"])
    sign_off = request.args.get("sign_off", defaults["sign_off"])
    weight = request.args.get("weight", defaults["weight"])
    footer = request.args.get("footer", defaults["footer"])
    custom_blocks = parse_custom_blocks(request.args.get("custom_blocks_json", defaults["custom_blocks_json"]))
    try:
        text1 = validate_text1_numeric(raw_text1, opts["field1_label"], opts["ui_language"])
        print_toggles = parse_print_toggles({
            "print_text2": request.args.get("print_text2", defaults["print_text2"]),
            "print_text3": request.args.get("print_text3", defaults["print_text3"]),
            "print_weight": request.args.get("print_weight", defaults["print_weight"]),
            "print_footer": request.args.get("print_footer", defaults["print_footer"]),
        })
        weight = validate_optional_numeric(weight, opts["weight_label"], opts["ui_language"])
        LOGGER.info("Generating PNG preview for payload inputs text1=%r text2=%r text3=%r sign_off=%r weight=%r footer=%r custom_blocks=%s toggles=%s", text1, text2, text3, sign_off, weight, footer, len(custom_blocks), print_toggles)
        img = render_label_image(text1, text2, text3, sign_off, weight, footer, custom_blocks, opts, preview=True, print_toggles=print_toggles)
        bio = BytesIO()
        img.save(bio, format="PNG", dpi=(203, 203), optimize=True)
        bio.seek(0)
        return send_file(bio, mimetype="image/png", download_name="label-preview.png")
    except Exception as exc:
        LOGGER.exception("PNG preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/api/print", methods=["POST"])
def api_print():
    opts = load_options()
    defaults = default_form_from_options(opts)
    payload = request.get_json(force=True, silent=False) or {}
    raw_text1 = payload.get("text1", defaults["text1"])
    text2 = str(payload.get("text2", defaults["text2"])).strip()
    text3 = str(payload.get("text3", defaults["text3"])).strip()
    sign_off = str(payload.get("sign_off", defaults["sign_off"])).strip()
    weight = digits_only(payload.get("weight", defaults["weight"]), defaults["weight"])
    footer = str(payload.get("footer", defaults["footer"])).strip()
    custom_blocks = parse_custom_blocks(payload.get("custom_blocks", payload.get("custom_blocks_json", [])))
    copies = max(1, min(50, int(payload.get("copies", 1))))
    print_toggles = parse_print_toggles({
        "print_text2": normalize_form_checkbox(payload.get("print_text2"), "1"),
        "print_text3": normalize_form_checkbox(payload.get("print_text3"), "1"),
        "print_weight": normalize_form_checkbox(payload.get("print_weight"), "0"),
        "print_footer": normalize_form_checkbox(payload.get("print_footer"), "1"),
    })
    try:
        text1 = validate_text1_numeric(raw_text1, opts["field1_label"], opts["ui_language"])
        weight = validate_optional_numeric(weight, opts["weight_label"], opts["ui_language"])
        zpl = build_zpl(text1, text2, text3, sign_off, weight, footer, custom_blocks, copies, opts, print_toggles=print_toggles)
        LOGGER.info("API print request received: copies=%s sign_off=%r weight=%r footer=%r custom_blocks=%s toggles=%s", copies, sign_off, weight, footer, len(custom_blocks), print_toggles)
        send_to_printer(opts["printer_host"], int(opts["printer_port"]), zpl)
        return jsonify({
            "ok": True,
            "printer": opts["printer_host"],
            "copies": copies,
            "qr_payload": build_qr_payload(text1, text2, text3, opts),
            "print_toggles": print_toggles,
            "custom_block_count": len(custom_blocks),
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
