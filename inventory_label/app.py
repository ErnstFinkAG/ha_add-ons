import json
import logging
import os
import re
import socket
from copy import deepcopy
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
FIELD_STORE_PATH = "/data/label_fields.json"
DEFAULT_TEXT_BLOCK_MARGIN_MM = 8.0
FIELD_GAP_MM = 4.0
FOOTER_GAP_MM = 3.0
SUPPORTED_UI_LANGUAGES = {"en", "de"}
SUPPORTED_ROTATIONS = {0, 90, 270}
ALIGNMENTS = {"left", "center", "right"}
FONT_FAMILIES = {"sans", "serif", "mono"}
FIELD_POSITIONS = {"body", "footer"}

DEFAULT_LABEL_PROFILES = [
    {
        "id": "standard",
        "name": "Standard",
        "printer_host": "",
        "printer_port": None,
        "label_width_mm": 170,
        "label_height_mm": 305,
        "qr_size_mm": 170,
        "top_margin_mm": 0,
        "footer_bottom_margin_mm": 0,
        "print_rotation_degrees": 0,
        "qr_quiet_zone_modules": 3,
        "qr_error_correction": "M",
    }
]

DEFAULT_PROFILE_FIELDS = {
    "standard": [
        {
            "id": "project_no",
            "name": "Projektnummer",
            "default_value": "250001",
            "alignment": "center",
            "font_family": "sans",
            "font_size_mm": 18,
            "bold": True,
            "italic": False,
            "underline": False,
            "print_by_default": True,
            "required": True,
            "number_only": True,
            "position": "body",
        },
        {
            "id": "project_name",
            "name": "Projektname",
            "default_value": "EFH Huggentobbler Biel",
            "alignment": "center",
            "font_family": "sans",
            "font_size_mm": 13,
            "bold": False,
            "italic": False,
            "underline": False,
            "print_by_default": True,
            "position": "body",
        },
        {
            "id": "element",
            "name": "Element",
            "default_value": "DE1",
            "alignment": "center",
            "font_family": "sans",
            "font_size_mm": 18,
            "bold": False,
            "italic": False,
            "underline": False,
            "print_by_default": True,
            "position": "body",
        },
        {
            "id": "weight",
            "name": "Gewicht",
            "default_value": "",
            "alignment": "center",
            "font_family": "sans",
            "font_size_mm": 7,
            "bold": False,
            "italic": False,
            "underline": False,
            "print_by_default": False,
            "number_only": True,
            "suffix": "kg",
            "position": "body",
        },
        {
            "id": "footer",
            "name": "Footer",
            "default_value": "Ernst Fink AG, Schorenweg 144, 4585 Biezwil",
            "alignment": "center",
            "font_family": "sans",
            "font_size_mm": 5,
            "bold": False,
            "italic": False,
            "underline": False,
            "print_by_default": True,
            "position": "footer",
            "footer_text": True,
            "append_current_date": True,
        },
    ]
}

DEFAULT_OPTIONS = {
    "ui_language": "de",
    "label_profiles": deepcopy(DEFAULT_LABEL_PROFILES),
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
        "intro_text": "Create label profiles in the add-on configuration. Field definitions are managed here in the web UI for the currently selected label.",
        "profile_select": "Label profile",
        "profile_none": "(none)",
        "qr_value_label": "QR fields",
        "qr_field_help": "Select one or more defined fields. The QR content is built automatically from their current values.",
        "qr_field_empty": "No QR field selected. No QR code will be generated.",
        "copies": "Copies",
        "configured_printer": "Configured printer",
        "not_configured": "Not configured",
        "printer_not_configured": "Printer host and port are not configured for this label profile.",
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
        "field_manager_heading": "Field manager",
        "field_manager_intro": "The active label has its own field submenu here. Add, edit, or delete fields without touching the add-on settings.",
        "save_field_button": "Save field",
        "new_field_button": "New field",
        "delete_field_button": "Delete",
        "edit_field_button": "Edit",
        "no_fields_configured": "No fields configured for this label yet.",
        "field_saved_message": "Field '{field}' saved for profile '{profile}'.",
        "field_deleted_message": "Field '{field}' deleted from profile '{profile}'.",
        "field_delete_failed": "Field delete failed: {error}",
        "field_save_failed": "Field save failed: {error}",
        "field_id_label": "Field ID",
        "field_name_label": "Field name",
        "default_value_label": "Default value",
        "alignment_label": "Alignment",
        "font_family_label": "Font family",
        "font_size_label": "Font size (mm)",
        "bold_label": "Bold",
        "italic_label": "Italic",
        "underline_label": "Underline",
        "print_by_default_label": "Print by default",
        "required_label": "Required when printed",
        "number_only_label": "Numbers only",
        "suffix_label": "Suffix",
        "append_current_date_label": "Append current date",
        "always_use_for_qr_label": "Always use for QR code",
        "footer_text_label": "Footer text (bottom anchored)",
        "value_options_label": "Value list",
        "value_options_help": "Optional suggestions, one value per line. Users can still enter any text.",
        "value_options_summary": "Choices",
        "max_lines_label": "Max lines",
        "field_summary_default": "Default",
        "field_summary_style": "Style",
        "field_summary_behavior": "Behavior",
        "field_editor_hint": "IDs are sanitized automatically and must be unique within the selected label.",
        "field_duplicate_error": "Field ID '{field_id}' already exists in this profile.",
        "field_name_required": "Field name is required.",
        "profile_not_found": "Label profile not found.",
        "legacy_migrated": "Legacy label_profiles_yaml was detected and migrated. Profiles now live in add-on settings, fields live in the web UI store.",
        "language_label": "Language",
        "profile_settings_source": "Profiles are defined in add-on settings",
    },
    "de": {
        "lang": "de",
        "page_title": "Inventory Label",
        "intro_text": "Lege die Etikettenprofile in der Add-on-Konfiguration an. Die Felddefinitionen werden hier in der Weboberfläche pro ausgewähltem Label verwaltet.",
        "profile_select": "Etikettenprofil",
        "profile_none": "(keins)",
        "qr_value_label": "QR-Felder",
        "qr_field_help": "Wähle ein oder mehrere definierte Felder. Der QR-Inhalt wird automatisch aus deren aktuellen Werten zusammengesetzt.",
        "qr_field_empty": "Kein QR-Feld ausgewählt. Es wird kein QR-Code erzeugt.",
        "copies": "Anzahl",
        "configured_printer": "Konfigurierter Drucker",
        "not_configured": "Nicht konfiguriert",
        "printer_not_configured": "Drucker-Host und Port sind für dieses Etikettenprofil nicht konfiguriert.",
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
        "field_manager_heading": "Feldverwaltung",
        "field_manager_intro": "Das aktive Label hat hier sein eigenes Untermenü. Felder können hinzugefügt, bearbeitet oder gelöscht werden, ohne die Add-on-Einstellungen anzufassen.",
        "save_field_button": "Feld speichern",
        "new_field_button": "Neues Feld",
        "delete_field_button": "Löschen",
        "edit_field_button": "Bearbeiten",
        "no_fields_configured": "Für dieses Label sind noch keine Felder konfiguriert.",
        "field_saved_message": "Feld '{field}' für Profil '{profile}' gespeichert.",
        "field_deleted_message": "Feld '{field}' aus Profil '{profile}' gelöscht.",
        "field_delete_failed": "Feld löschen fehlgeschlagen: {error}",
        "field_save_failed": "Feld speichern fehlgeschlagen: {error}",
        "field_id_label": "Feld-ID",
        "field_name_label": "Feldname",
        "default_value_label": "Standardwert",
        "alignment_label": "Ausrichtung",
        "font_family_label": "Schriftfamilie",
        "font_size_label": "Schriftgröße (mm)",
        "bold_label": "Fett",
        "italic_label": "Kursiv",
        "underline_label": "Unterstrichen",
        "print_by_default_label": "Standardmäßig drucken",
        "required_label": "Pflicht wenn gedruckt",
        "number_only_label": "Nur Zahlen",
        "suffix_label": "Suffix",
        "append_current_date_label": "Aktuelles Datum anhängen",
        "always_use_for_qr_label": "Immer für QR-Code verwenden",
        "footer_text_label": "Footer-Text (unten verankert)",
        "value_options_label": "Werteliste",
        "value_options_help": "Optionale Vorschläge, ein Wert pro Zeile. Freitext bleibt weiterhin möglich.",
        "value_options_summary": "Auswahlwerte",
        "max_lines_label": "Max. Zeilen",
        "field_summary_default": "Standard",
        "field_summary_style": "Stil",
        "field_summary_behavior": "Verhalten",
        "field_editor_hint": "IDs werden automatisch bereinigt und müssen innerhalb des ausgewählten Labels eindeutig sein.",
        "field_duplicate_error": "Feld-ID '{field_id}' existiert in diesem Profil bereits.",
        "field_name_required": "Feldname ist erforderlich.",
        "profile_not_found": "Etikettenprofil nicht gefunden.",
        "legacy_migrated": "Altes label_profiles_yaml erkannt und migriert. Profile liegen jetzt in den Add-on-Einstellungen, Felder im Web-UI-Speicher.",
        "language_label": "Sprache",
        "profile_settings_source": "Profile werden in den Add-on-Einstellungen definiert",
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
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    .wrap { max-width: 1250px; margin: 0 auto; padding: 24px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); margin-bottom: 20px; }
    h1, h2, h3 { margin-top: 0; }
    label { display: block; font-weight: 600; margin-bottom: 8px; }
    input, select, textarea { width: 100%; border-radius: 12px; border: 1px solid var(--border); background: #0f172a; color: var(--text); padding: 12px 14px; font: inherit; margin-bottom: 16px; }
    textarea { min-height: 110px; resize: vertical; }
    input[type="checkbox"] { width: auto; margin: 0; accent-color: var(--accent); }
    .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
    .row-compact { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .btns { display: flex; gap: 12px; flex-wrap: wrap; }
    button, .button-link { border: none; background: var(--accent); color: white; padding: 12px 18px; border-radius: 12px; font: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
    button.secondary, .button-link.secondary { background: transparent; border: 1px solid var(--border); }
    button.danger { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); }
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
    .two-col { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(360px, 0.9fr); gap: 20px; }
    .editor { background: #111827; border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
    .editor .btns { margin-top: 4px; }
    .tag-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .tag { display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px; background: #0f172a; border: 1px solid var(--border); color: var(--muted); font-size: 0.9rem; }
    .field-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    .small { font-size: 0.92rem; }
    .headline-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
    .selector-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .selector-option { display: flex; gap: 10px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 14px; background: #111827; cursor: pointer; }
    .selector-option input { margin-top: 3px; margin-bottom: 0; }
    .selector-text { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .selector-text strong, .selector-text span { word-break: break-word; }
    code { word-break: break-word; }
    @media (max-width: 980px) {
      .two-col { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="headline-row">
        <div>
          <h1>{{ ui.page_title }}</h1>
          <p class="muted">{{ ui.intro_text }}</p>
        </div>
      </div>
      {% if result %}
        <div class="flash {{ 'ok' if result.success else 'error' }}">{{ result.message }}</div>
      {% endif %}
      {% if field_result %}
        <div class="flash {{ 'ok' if field_result.success else 'error' }}">{{ field_result.message }}</div>
      {% endif %}
      <form id="label-form" method="post" action="{{ ingress_base }}/print">
        <label for="profile_id">{{ ui.profile_select }}</label>
        <select id="profile_id" name="profile_id">
          {% for profile in label_profiles %}
            <option value="{{ profile.id }}" {% if profile.id == active_profile_id %}selected{% endif %}>{{ profile.name }}</option>
          {% endfor %}
        </select>

        <div class="row">
          <div>
            <label>{{ ui.qr_value_label }}</label>
            <div class="selector-grid">
              {% for field in qr_field_options %}
              <label class="selector-option" for="qr_field_{{ field.id }}">
                <input id="qr_field_{{ field.id }}" name="qr_field_ids" type="checkbox" value="{{ field.id }}" data-field-id="{{ field.id }}" {% if field.selected %}checked{% endif %}>
                <div class="selector-text">
                  <strong>{{ field.name }}</strong>
                  <span class="muted small">{{ field.value or ui.none }}</span>
                </div>
              </label>
              {% else %}
              <div class="field-card muted">{{ ui.no_fields_configured }}</div>
              {% endfor %}
            </div>
            <p class="muted small">{{ ui.qr_field_help }}</p>
            {% if not qr_selected_ids %}
            <p class="muted small">{{ ui.qr_field_empty }}</p>
            {% endif %}
          </div>
          <div>
            <label for="copies">{{ ui.copies }}</label>
            <input id="copies" name="copies" type="number" min="1" max="50" value="{{ form.copies }}" required>
          </div>
        </div>

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
              <input id="print_{{ field.id }}" name="print_{{ field.id }}" type="checkbox" value="1" data-field-id="{{ field.id }}" {% if field.print_enabled %}checked{% endif %}>
              <label for="print_{{ field.id }}" style="margin:0; font-weight:500;">{{ ui.print_field }}</label>
            </div>
            <input
              id="field_{{ field.id }}"
              name="field_{{ field.id }}"
              type="text"
              value="{{ field.value }}"
              {% if field.value_options %}list="field_options_{{ field.id }}"{% endif %}
              {% if field.number_only %}inputmode="numeric" pattern="[0-9]*" data-number-only="1"{% endif %}
            >
            {% if field.value_options %}
            <datalist id="field_options_{{ field.id }}">
              {% for option in field.value_options %}
              <option value="{{ option }}"></option>
              {% endfor %}
            </datalist>
            {% endif %}
          </div>
          {% else %}
          <div class="field-card muted">{{ ui.no_fields_configured }}</div>
          {% endfor %}
        </div>

        <div class="row">
          <div>
            <label>{{ ui.configured_printer }}</label>
            <input value="{{ printer_target }}" disabled>
          </div>
          <div>
            <label>{{ ui.profile_settings_source }}</label>
            <input value="{{ active_profile_name or ui.profile_none }}" disabled>
          </div>
        </div>

        <div class="btns">
          <button type="submit">{{ ui.print_label_button }}</button>
          <a id="preview-zpl-link" class="button-link secondary" href="{{ ingress_base }}/preview?{{ preview_query }}">{{ ui.preview_zpl }}</a>
          <a id="preview-png-link" class="button-link secondary" href="{{ ingress_base }}/preview.png?{{ preview_query }}" target="_blank" rel="noopener">{{ ui.open_png_preview }}</a>
        </div>
      </form>
    </div>

    <div class="two-col">
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
          <li><strong>{{ ui.language_label }}:</strong> <code>{{ ui.lang }}</code></li>
        </ul>
        {% if width_warning %}
        <p class="muted">{{ ui.width_warning }}</p>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h2>{{ ui.field_manager_heading }}</h2>
      <p class="muted">{{ ui.field_manager_intro }}</p>

      <div class="field-grid">
        {% for field in active_profile_fields %}
        <div class="field-card">
          <div class="headline-row">
            <h3>{{ field.name }}</h3>
            <code>{{ field.id }}</code>
          </div>
          <div class="tag-list">
            <span class="tag">{{ ui.position }}: {{ ui.position_footer if field.position == 'footer' else ui.position_body }}</span>
            <span class="tag">{{ ui.field_summary_default }}: {{ field.default_value or ui.none }}</span>
            <span class="tag">{{ ui.field_summary_style }}: {{ field.font_family }} / {{ field.font_size_mm }} mm / {{ field.alignment }}</span>
            <span class="tag">{{ ui.field_summary_behavior }}: {% if field.print_by_default %}{{ ui.print_field }}{% else %}{{ ui.none }}{% endif %}</span>
            {% if field.required %}<span class="tag">{{ ui.required }}</span>{% endif %}
            {% if field.number_only %}<span class="tag">{{ ui.numeric_only }}</span>{% endif %}
            {% if field.suffix %}<span class="tag">{{ field.suffix }}</span>{% endif %}
            {% if field.always_use_for_qr %}<span class="tag">QR</span>{% endif %}
            {% if field.footer_text %}<span class="tag">{{ ui.footer_text_label }}</span>{% endif %}
            {% if field.value_options %}<span class="tag">{{ ui.value_options_summary }}: {{ field.value_options|length }}</span>{% endif %}
            {% if field.append_current_date %}<span class="tag">Date</span>{% endif %}
          </div>
          <div class="field-actions">
            <button type="button" class="secondary edit-field-button" data-field-id="{{ field.id }}">{{ ui.edit_field_button }}</button>
            <form method="post" action="{{ ingress_base }}/fields/delete" style="margin:0;">
              <input type="hidden" name="profile_id" value="{{ active_profile_id }}">
              <input type="hidden" name="field_id" value="{{ field.id }}">
              <button type="submit" class="danger">{{ ui.delete_field_button }}</button>
            </form>
          </div>
        </div>
        {% else %}
        <div class="field-card muted">{{ ui.no_fields_configured }}</div>
        {% endfor %}
      </div>

      <div class="editor" style="margin-top: 18px;">
        <form id="field-editor-form" method="post" action="{{ ingress_base }}/fields/save">
          <input type="hidden" name="profile_id" value="{{ active_profile_id }}">
          <input type="hidden" id="original_field_id" name="original_field_id" value="{{ editor_form.original_field_id }}">

          <div class="headline-row">
            <h3>{{ ui.save_field_button }}</h3>
            <button type="button" id="new-field-button" class="secondary">{{ ui.new_field_button }}</button>
          </div>
          <p class="muted small">{{ ui.field_editor_hint }}</p>

          <div class="row">
            <div>
              <label for="editor_name">{{ ui.field_name_label }}</label>
              <input id="editor_name" name="name" type="text" value="{{ editor_form.name }}" required>
            </div>
            <div>
              <label for="editor_id">{{ ui.field_id_label }}</label>
              <input id="editor_id" name="id" type="text" value="{{ editor_form.id }}">
            </div>
          </div>

          <div class="row">
            <div>
              <label for="editor_default_value">{{ ui.default_value_label }}</label>
              <input id="editor_default_value" name="default_value" type="text" value="{{ editor_form.default_value }}">
            </div>
            <div>
              <label for="editor_suffix">{{ ui.suffix_label }}</label>
              <input id="editor_suffix" name="suffix" type="text" value="{{ editor_form.suffix }}">
            </div>
          </div>

          <div>
            <label for="editor_value_options_text">{{ ui.value_options_label }}</label>
            <textarea id="editor_value_options_text" name="value_options_text">{{ editor_form.value_options_text }}</textarea>
            <p class="muted small">{{ ui.value_options_help }}</p>
          </div>

          <div class="row-compact">
            <div>
              <label for="editor_alignment">{{ ui.alignment_label }}</label>
              <select id="editor_alignment" name="alignment">
                {% for value in alignments %}
                <option value="{{ value }}" {% if editor_form.alignment == value %}selected{% endif %}>{{ value }}</option>
                {% endfor %}
              </select>
            </div>
            <div>
              <label for="editor_font_family">{{ ui.font_family_label }}</label>
              <select id="editor_font_family" name="font_family">
                {% for value in font_families %}
                <option value="{{ value }}" {% if editor_form.font_family == value %}selected{% endif %}>{{ value }}</option>
                {% endfor %}
              </select>
            </div>
            <div>
              <label for="editor_font_size_mm">{{ ui.font_size_label }}</label>
              <input id="editor_font_size_mm" name="font_size_mm" type="number" min="2" max="30" step="0.5" value="{{ editor_form.font_size_mm }}">
            </div>
            <div>
              <label for="editor_position">{{ ui.position }}</label>
              <select id="editor_position" name="position">
                {% for value in field_positions %}
                <option value="{{ value }}" {% if editor_form.position == value %}selected{% endif %}>{{ ui.position_footer if value == 'footer' else ui.position_body }}</option>
                {% endfor %}
              </select>
            </div>
            <div>
              <label for="editor_max_lines">{{ ui.max_lines_label }}</label>
              <input id="editor_max_lines" name="max_lines" type="number" min="1" max="8" step="1" value="{{ editor_form.max_lines }}">
            </div>
          </div>

          <div class="row-compact">
            <label class="checkline"><input id="editor_bold" name="bold" type="checkbox" value="1" {% if editor_form.bold %}checked{% endif %}> {{ ui.bold_label }}</label>
            <label class="checkline"><input id="editor_italic" name="italic" type="checkbox" value="1" {% if editor_form.italic %}checked{% endif %}> {{ ui.italic_label }}</label>
            <label class="checkline"><input id="editor_underline" name="underline" type="checkbox" value="1" {% if editor_form.underline %}checked{% endif %}> {{ ui.underline_label }}</label>
            <label class="checkline"><input id="editor_print_by_default" name="print_by_default" type="checkbox" value="1" {% if editor_form.print_by_default %}checked{% endif %}> {{ ui.print_by_default_label }}</label>
            <label class="checkline"><input id="editor_required" name="required" type="checkbox" value="1" {% if editor_form.required %}checked{% endif %}> {{ ui.required_label }}</label>
            <label class="checkline"><input id="editor_number_only" name="number_only" type="checkbox" value="1" {% if editor_form.number_only %}checked{% endif %}> {{ ui.number_only_label }}</label>
            <label class="checkline"><input id="editor_append_current_date" name="append_current_date" type="checkbox" value="1" {% if editor_form.append_current_date %}checked{% endif %}> {{ ui.append_current_date_label }}</label>
            <label class="checkline"><input id="editor_always_use_for_qr" name="always_use_for_qr" type="checkbox" value="1" {% if editor_form.always_use_for_qr %}checked{% endif %}> {{ ui.always_use_for_qr_label }}</label>
            <label class="checkline"><input id="editor_footer_text" name="footer_text" type="checkbox" value="1" {% if editor_form.footer_text %}checked{% endif %}> {{ ui.footer_text_label }}</label>
          </div>

          <div class="btns">
            <button type="submit">{{ ui.save_field_button }}</button>
          </div>
        </form>
      </div>
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
      const newFieldButton = document.getElementById("new-field-button");
      const fieldEditorForm = document.getElementById("field-editor-form");
      const fieldData = {{ field_editor_json|tojson }};
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
          params.append(key, String(value));
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

      function persistFieldCheckbox(fieldId, settingKey, checked) {
        if (!fieldId || !settingKey) return;
        const body = new URLSearchParams();
        body.set("profile_id", profileSelect ? (profileSelect.value || "") : "");
        body.set("field_id", fieldId);
        body.set("setting", settingKey);
        body.set("value", checked ? "1" : "0");
        fetch(`${ingressBase}/fields/quick-update`, {
          method: "POST",
          headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
          body: body.toString(),
        }).then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          if (fieldData[fieldId]) fieldData[fieldId][settingKey] = !!checked;
        }).catch((error) => {
          console.warn("Failed to persist field checkbox", fieldId, settingKey, error);
        });
      }

      function setCheckbox(id, value) {
        const el = document.getElementById(id);
        if (el) el.checked = !!value;
      }

      function setValue(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value == null ? "" : String(value);
      }

      function resetFieldEditor() {
        if (!fieldEditorForm) return;
        setValue("original_field_id", "");
        setValue("editor_name", "");
        setValue("editor_id", "");
        setValue("editor_default_value", "");
        setValue("editor_suffix", "");
        setValue("editor_alignment", "center");
        setValue("editor_font_family", "sans");
        setValue("editor_font_size_mm", "7.0");
        setValue("editor_position", "body");
        setValue("editor_max_lines", "3");
        setValue("editor_value_options_text", "");
        setCheckbox("editor_bold", false);
        setCheckbox("editor_italic", false);
        setCheckbox("editor_underline", false);
        setCheckbox("editor_print_by_default", true);
        setCheckbox("editor_required", false);
        setCheckbox("editor_number_only", false);
        setCheckbox("editor_append_current_date", false);
        setCheckbox("editor_always_use_for_qr", false);
        setCheckbox("editor_footer_text", false);
      }

      function loadFieldIntoEditor(fieldId) {
        const data = fieldData[fieldId];
        if (!data) return;
        setValue("original_field_id", data.id || "");
        setValue("editor_name", data.name || "");
        setValue("editor_id", data.id || "");
        setValue("editor_default_value", data.default_value || "");
        setValue("editor_suffix", data.suffix || "");
        setValue("editor_alignment", data.alignment || "center");
        setValue("editor_font_family", data.font_family || "sans");
        setValue("editor_font_size_mm", data.font_size_mm || "7.0");
        setValue("editor_position", data.position || "body");
        setValue("editor_max_lines", data.max_lines || "3");
        setValue("editor_value_options_text", data.value_options_text || "");
        setCheckbox("editor_bold", data.bold);
        setCheckbox("editor_italic", data.italic);
        setCheckbox("editor_underline", data.underline);
        setCheckbox("editor_print_by_default", data.print_by_default);
        setCheckbox("editor_required", data.required);
        setCheckbox("editor_number_only", data.number_only);
        setCheckbox("editor_append_current_date", data.append_current_date);
        setCheckbox("editor_always_use_for_qr", data.always_use_for_qr);
        setCheckbox("editor_footer_text", data.footer_text);
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
          input.addEventListener("click", () => {
            applyPreviewUpdate();
            const fieldId = input.getAttribute("data-field-id") || "";
            if (fieldId && input.name === `print_${fieldId}`) persistFieldCheckbox(fieldId, "print_by_default", input.checked);
            if (fieldId && input.name === "qr_field_ids") persistFieldCheckbox(fieldId, "always_use_for_qr", input.checked);
          });
        } else {
          input.addEventListener("input", schedulePreviewUpdate);
          input.addEventListener("change", applyPreviewUpdate);
        }
      });

      const footerTextCheckbox = document.getElementById("editor_footer_text");
      const editorPositionSelect = document.getElementById("editor_position");
      if (footerTextCheckbox && editorPositionSelect) {
        footerTextCheckbox.addEventListener("change", () => {
          editorPositionSelect.value = footerTextCheckbox.checked ? "footer" : "body";
        });
        editorPositionSelect.addEventListener("change", () => {
          footerTextCheckbox.checked = editorPositionSelect.value === "footer";
        });
      }

      document.querySelectorAll(".edit-field-button").forEach((button) => {
        button.addEventListener("click", () => {
          const fieldId = button.getAttribute("data-field-id");
          loadFieldIntoEditor(fieldId || "");
          if (fieldEditorForm) fieldEditorForm.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      });

      if (newFieldButton) {
        newFieldButton.addEventListener("click", () => {
          resetFieldEditor();
          const nameInput = document.getElementById("editor_name");
          if (nameInput) nameInput.focus();
        });
      }

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


def normalize_optional_port(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except Exception:
        return None
    if 1 <= parsed <= 65535:
        return parsed
    return None


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


def normalize_profile_field(raw: object, idx: int) -> Dict:
    data = raw if isinstance(raw, dict) else {}
    name = normalize_string(data.get("name"), f"Field {idx}")
    field_id = sanitize_id(str(data.get("id") or name), f"field_{idx}")
    position = normalize_position(data.get("position"), "body")
    footer_text = normalize_bool(data.get("footer_text"), position == "footer")
    if footer_text:
        position = "footer"
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
        "position": position,
        "footer_text": footer_text,
        "append_current_date": normalize_bool(data.get("append_current_date"), False),
        "always_use_for_qr": normalize_bool(data.get("always_use_for_qr"), False),
        "value_options": normalize_value_options(data.get("value_options")),
        "max_lines": normalize_int(data.get("max_lines"), 3, 1, 8),
    }


def normalize_profile(raw: object, idx: int) -> Dict:
    data = raw if isinstance(raw, dict) else {}
    name = normalize_string(data.get("name"), f"Label {idx}")
    profile_id = sanitize_id(str(data.get("id") or name), f"label_{idx}")
    return {
        "id": profile_id,
        "name": name,
        "printer_host": normalize_string(data.get("printer_host"), ""),
        "printer_port": normalize_optional_port(data.get("printer_port")),
        "label_width_mm": normalize_float(data.get("label_width_mm"), 170.0, 50.0, 500.0),
        "label_height_mm": normalize_float(data.get("label_height_mm"), 305.0, 50.0, 1000.0),
        "qr_size_mm": normalize_float(data.get("qr_size_mm"), 170.0, 10.0, 300.0),
        "top_margin_mm": normalize_float(data.get("top_margin_mm"), 0.0, 0.0, 100.0),
        "footer_bottom_margin_mm": normalize_float(data.get("footer_bottom_margin_mm"), 0.0, 0.0, 50.0),
        "print_rotation_degrees": normalize_rotation_degrees(data.get("print_rotation_degrees"), 0),
        "qr_default_value": "" if data.get("qr_default_value") is None else str(data.get("qr_default_value")),
        "qr_quiet_zone_modules": normalize_int(data.get("qr_quiet_zone_modules"), 3, 0, 20),
        "qr_error_correction": str(data.get("qr_error_correction") or "M").strip().upper() if str(data.get("qr_error_correction") or "M").strip().upper() in QR_ERROR_CORRECTION_MAP else "M",
    }


def parse_label_profiles(raw: object) -> List[Dict]:
    data = raw
    if isinstance(data, dict):
        for key in ("label_profiles", "profiles", "labels"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return [normalize_profile(item, idx) for idx, item in enumerate(data, start=1) if isinstance(item, dict)]


def parse_legacy_profiles_yaml(raw: object) -> Tuple[List[Dict], Dict[str, List[Dict]]]:
    source = str(raw or "").strip()
    if not source:
        return [], {}
    try:
        loaded = yaml.safe_load(source) or []
    except Exception as exc:
        LOGGER.warning("Failed to parse legacy label_profiles_yaml: %s", exc)
        return [], {}
    if isinstance(loaded, dict):
        for key in ("label_profiles", "profiles", "labels"):
            if isinstance(loaded.get(key), list):
                loaded = loaded[key]
                break
        else:
            loaded = [loaded]
    if not isinstance(loaded, list):
        return [], {}

    profiles: List[Dict] = []
    field_store: Dict[str, List[Dict]] = {}
    for idx, item in enumerate(loaded, start=1):
        if not isinstance(item, dict):
            continue
        profile = normalize_profile(item, idx)
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        field_store[profile["id"]] = [normalize_profile_field(field, field_idx) for field_idx, field in enumerate(fields, start=1)]
        profiles.append(profile)
    return profiles, field_store


def load_options() -> Tuple[Dict, Dict[str, List[Dict]], str | None]:
    options = dict(DEFAULT_OPTIONS)
    raw = {}
    migrated_notice = None
    if os.path.exists(OPTIONS_PATH):
        try:
            with open(OPTIONS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                raw = data
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", OPTIONS_PATH, exc)

    options["ui_language"] = normalize_ui_language(raw.get("ui_language"), DEFAULT_OPTIONS["ui_language"])

    legacy_field_store: Dict[str, List[Dict]] = {}
    if isinstance(raw.get("label_profiles"), list):
        profiles = parse_label_profiles(raw.get("label_profiles"))
    else:
        profiles = []

    if not profiles:
        legacy_profiles, legacy_field_store = parse_legacy_profiles_yaml(raw.get("label_profiles_yaml"))
        if legacy_profiles:
            profiles = legacy_profiles
            migrated_notice = "legacy_migrated"

    if not profiles:
        profiles = parse_label_profiles(DEFAULT_OPTIONS["label_profiles"])

    options["label_profiles"] = profiles
    return options, legacy_field_store, migrated_notice


def default_field_store_for_profiles(profiles: List[Dict]) -> Dict[str, List[Dict]]:
    store: Dict[str, List[Dict]] = {}
    for profile in profiles:
        defaults = DEFAULT_PROFILE_FIELDS.get(profile["id"], [])
        store[profile["id"]] = [normalize_profile_field(field, idx) for idx, field in enumerate(defaults, start=1)]
    return store


def load_field_store(profiles: List[Dict], legacy_seed: Dict[str, List[Dict]] | None = None) -> Dict[str, List[Dict]]:
    profile_ids = {profile["id"] for profile in profiles}
    store: Dict[str, List[Dict]] = {}
    wrote_file = False
    if os.path.exists(FIELD_STORE_PATH):
        try:
            with open(FIELD_STORE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                for profile_id, fields in data.items():
                    if profile_id not in profile_ids or not isinstance(fields, list):
                        continue
                    store[profile_id] = [normalize_profile_field(field, idx) for idx, field in enumerate(fields, start=1)]
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", FIELD_STORE_PATH, exc)

    defaults = default_field_store_for_profiles(profiles)
    legacy_seed = legacy_seed or {}
    for profile in profiles:
        profile_id = profile["id"]
        if profile_id in store:
            continue
        seed_fields = legacy_seed.get(profile_id)
        if seed_fields:
            store[profile_id] = [normalize_profile_field(field, idx) for idx, field in enumerate(seed_fields, start=1)]
            wrote_file = True
        else:
            store[profile_id] = defaults.get(profile_id, [])
            if defaults.get(profile_id):
                wrote_file = True

    # Keep only active profile ids.
    store = {profile_id: store.get(profile_id, []) for profile_id in sorted(profile_ids)}
    if wrote_file or not os.path.exists(FIELD_STORE_PATH):
        save_field_store(store)
    return store


def save_field_store(store: Dict[str, List[Dict]]) -> None:
    serializable = {
        profile_id: [normalize_profile_field(field, idx) for idx, field in enumerate(fields, start=1)]
        for profile_id, fields in store.items()
    }
    with open(FIELD_STORE_PATH, "w", encoding="utf-8") as handle:
        json.dump(serializable, handle, ensure_ascii=False, indent=2)


def load_runtime_options(profile_id: str | None = None) -> Dict:
    opts, legacy_seed, migration_notice = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    if not profiles:
        profiles = parse_label_profiles(DEFAULT_OPTIONS["label_profiles"])
    field_store = load_field_store(profiles, legacy_seed)

    selected_id = profile_id or request.values.get("profile_id") or request.args.get("profile_id") or request.form.get("profile_id")
    active_profile = None
    if selected_id:
        active_profile = next((profile for profile in profiles if profile["id"] == selected_id), None)
    if active_profile is None and profiles:
        active_profile = profiles[0]

    enriched_profiles = []
    for profile in profiles:
        enriched_profiles.append({**profile, "fields": deepcopy(field_store.get(profile["id"], []))})

    if active_profile:
        active_profile = next((profile for profile in enriched_profiles if profile["id"] == active_profile["id"]), active_profile)

    opts["label_profiles"] = enriched_profiles
    opts["active_profile"] = active_profile
    opts["active_profile_id"] = active_profile["id"] if active_profile else ""
    opts["active_profile_name"] = active_profile["name"] if active_profile else ""
    opts["field_store"] = field_store
    opts["migration_notice"] = migration_notice
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


def default_form_from_profile(profile: Dict | None) -> Dict[str, object]:
    form: Dict[str, object] = {
        "profile_id": profile["id"] if profile else "",
        "qr_field_ids": [],
        "copies": "1",
    }
    for field in (profile or {}).get("fields", []):
        form[field_value_name(field["id"])] = field["default_value"]
        if field["print_by_default"]:
            form[field_print_name(field["id"])] = "1"
    return form


def form_data_from_request(opts: Dict) -> Tuple[Dict[str, object], List[Dict]]:
    profile = opts.get("active_profile") or {}
    defaults = default_form_from_profile(profile)
    selected_qr_fields = selected_qr_field_ids_from_source(profile, request.values)
    form: Dict[str, object] = {
        "profile_id": request.values.get("profile_id", defaults.get("profile_id", "")),
        "qr_field_ids": selected_qr_fields,
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


def normalize_qr_value(value: object) -> str:
    return str(value if value is not None else "").strip()


def normalize_value_options(value: object) -> List[str]:
    if value is None:
        return []
    items: List[str] = []
    if isinstance(value, str):
        normalized_text = value.replace("\r", "\n")
        split_values = normalized_text.split("\n") if "\n" in normalized_text else normalized_text.split(",")
        items = [part.strip() for part in split_values]
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if item is None:
                continue
            text_value = str(item).strip()
            if text_value:
                items.append(text_value)
    else:
        text_value = str(value).strip()
        if text_value:
            items.append(text_value)
    result: List[str] = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def value_options_text(value: object) -> str:
    return "\n".join(normalize_value_options(value))


def normalize_qr_field_ids(values: object) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    elif isinstance(values, (list, tuple, set)):
        raw_values = []
        for item in values:
            if isinstance(item, str):
                raw_values.extend(part.strip() for part in item.split(","))
            elif item is not None:
                raw_values.append(str(item).strip())
    else:
        raw_values = [str(values).strip()]
    result: List[str] = []
    seen = set()
    for item in raw_values:
        normalized = sanitize_id(item, "")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def selected_qr_field_ids_from_source(profile: Dict, source: object) -> List[str]:
    values: object = []
    if hasattr(source, "getlist"):
        values = source.getlist("qr_field_ids")
    elif isinstance(source, dict):
        values = source.get("qr_field_ids", source.get("qr_fields", []))
    selected = normalize_qr_field_ids(values)
    if not selected:
        selected = [field.get("id") for field in profile.get("fields", []) if normalize_bool(field.get("always_use_for_qr"), False)]
    valid_ids = {field.get("id") for field in profile.get("fields", [])}
    return [field_id for field_id in selected if field_id in valid_ids]


def qr_payload_from_field_forms(field_forms: List[Dict], selected_field_ids: List[str]) -> str:
    selected_lookup = set(normalize_qr_field_ids(selected_field_ids))
    if not selected_lookup:
        return ""
    parts: List[str] = []
    for field in field_forms:
        if field.get("id") not in selected_lookup:
            continue
        value = normalize_qr_value(field.get("value", ""))
        if value:
            parts.append(value)
    return " - ".join(parts)


def format_printer_target(profile: Dict, language_or_options: object) -> str:
    host = normalize_string(profile.get("printer_host"), "")
    port = profile.get("printer_port")
    if not host or port in (None, "", 0):
        return ui_text(language_or_options, "not_configured")
    return f"{host}:{port}"


def resolve_printer_target(profile: Dict, language_or_options: object) -> Tuple[str, int]:
    host = normalize_string(profile.get("printer_host"), "")
    port = normalize_optional_port(profile.get("printer_port"))
    if not host or port is None:
        raise ValueError(ui_text(language_or_options, "printer_not_configured"))
    return host, port


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
    has_qr = bool(normalize_qr_value(qr_value))
    margin_x = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    text_width = max(1, printable_w - (margin_x * 2))
    current_y = layout["top_margin_dots"]
    if has_qr:
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
    has_qr = bool(normalize_qr_value(qr_value))
    left_margin = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    right_margin = mm_to_dots(DEFAULT_TEXT_BLOCK_MARGIN_MM)
    text_left = left_margin
    text_width = max(1, logical_w - text_left - right_margin)
    text_top = layout["top_margin_dots"]
    if has_qr:
        qr_size = min(layout["qr_size_dots"], logical_h)
        qr_left = min(max(layout["top_margin_dots"], 0), max(0, logical_w - qr_size))
        qr_top = max((logical_h - qr_size) // 2, 0)
        qr_img = build_qr_image(qr_value, qr_size, profile).convert("RGB")
        landscape.paste(qr_img, (qr_left, qr_top))
        if preview:
            preview_border_width = max(2, int(round(DOTS_PER_MM * 0.5)))
            draw.rectangle((qr_left, qr_top, qr_left + qr_size - 1, qr_top + qr_size - 1), outline=(220, 38, 38), width=preview_border_width)
        inter_block_gap = mm_to_dots(8)
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


def preview_query_from_form(form: Dict[str, object], field_forms: List[Dict]) -> str:
    params: List[Tuple[str, str]] = [
        ("profile_id", str(form.get("profile_id", ""))),
        ("copies", str(form.get("copies", "1"))),
    ]
    for field_id in normalize_qr_field_ids(form.get("qr_field_ids", [])):
        params.append(("qr_field_ids", field_id))
    for field in field_forms:
        params.append((field_value_name(field["id"]), str(field.get("value", ""))))
        if field.get("print_enabled"):
            params.append((field_print_name(field["id"]), "1"))
    return urlencode(params, doseq=True)


def blank_editor_form() -> Dict:
    return {
        "original_field_id": "",
        "id": "",
        "name": "",
        "default_value": "",
        "alignment": "center",
        "font_family": "sans",
        "font_size_mm": 7.0,
        "bold": False,
        "italic": False,
        "underline": False,
        "print_by_default": True,
        "required": False,
        "number_only": False,
        "suffix": "",
        "position": "body",
        "footer_text": False,
        "append_current_date": False,
        "always_use_for_qr": False,
        "value_options": [],
        "value_options_text": "",
        "max_lines": 3,
    }


def editor_form_from_field(field: Dict | None) -> Dict:
    if not field:
        return blank_editor_form()
    return {
        "original_field_id": field.get("id", ""),
        "id": field.get("id", ""),
        "name": field.get("name", ""),
        "default_value": field.get("default_value", ""),
        "alignment": field.get("alignment", "center"),
        "font_family": field.get("font_family", "sans"),
        "font_size_mm": field.get("font_size_mm", 7.0),
        "bold": field.get("bold", False),
        "italic": field.get("italic", False),
        "underline": field.get("underline", False),
        "print_by_default": field.get("print_by_default", True),
        "required": field.get("required", False),
        "number_only": field.get("number_only", False),
        "suffix": field.get("suffix", ""),
        "position": field.get("position", "body"),
        "footer_text": normalize_bool(field.get("footer_text"), field.get("position") == "footer"),
        "append_current_date": field.get("append_current_date", False),
        "always_use_for_qr": field.get("always_use_for_qr", False),
        "value_options": normalize_value_options(field.get("value_options", [])),
        "value_options_text": value_options_text(field.get("value_options", [])),
        "max_lines": field.get("max_lines", 3),
    }


def field_store_map_for_profile(profile: Dict) -> Dict[str, Dict]:
    return {field["id"]: editor_form_from_field(field) for field in profile.get("fields", [])}


def validate_and_normalize_editor_payload(source: Dict, language: str) -> Tuple[str, Dict]:
    raw_name = normalize_string(source.get("name"), "")
    if not raw_name:
        raise ValueError(ui_text(language, "field_name_required"))
    original_field_id = sanitize_id(str(source.get("original_field_id") or ""), "")
    normalized = normalize_profile_field(
        {
            "id": source.get("id") or raw_name,
            "name": raw_name,
            "default_value": source.get("default_value", ""),
            "alignment": source.get("alignment", "center"),
            "font_family": source.get("font_family", "sans"),
            "font_size_mm": source.get("font_size_mm", 7.0),
            "bold": source.get("bold"),
            "italic": source.get("italic"),
            "underline": source.get("underline"),
            "print_by_default": source.get("print_by_default", "1"),
            "required": source.get("required"),
            "number_only": source.get("number_only"),
            "suffix": source.get("suffix", ""),
            "position": source.get("position", "body"),
            "footer_text": source.get("footer_text"),
            "append_current_date": source.get("append_current_date"),
            "always_use_for_qr": source.get("always_use_for_qr"),
            "value_options": normalize_value_options(source.get("value_options_text", source.get("value_options", []))),
            "max_lines": source.get("max_lines", 3),
        },
        1,
    )
    return original_field_id, normalized


def save_profile_field(profile_id: str, original_field_id: str, field: Dict, profile_name: str, language: str) -> None:
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    field_store = load_field_store(profiles)
    fields = list(field_store.get(profile_id, []))
    new_id = field["id"]
    collision = next((existing for existing in fields if existing["id"] == new_id and existing["id"] != original_field_id), None)
    if collision:
        raise ValueError(ui_text(language, "field_duplicate_error", field_id=new_id))

    updated = False
    for idx, existing in enumerate(fields):
        if existing["id"] == original_field_id and original_field_id:
            fields[idx] = field
            updated = True
            break
    if not updated:
        fields.append(field)
    field_store[profile_id] = fields
    save_field_store(field_store)
    LOGGER.info("Saved field %s for profile %s (%s)", field["id"], profile_id, profile_name)


def delete_profile_field(profile_id: str, field_id: str, profile_name: str) -> bool:
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    field_store = load_field_store(profiles)
    fields = list(field_store.get(profile_id, []))
    remaining = [field for field in fields if field.get("id") != field_id]
    changed = len(remaining) != len(fields)
    field_store[profile_id] = remaining
    save_field_store(field_store)
    if changed:
        LOGGER.info("Deleted field %s from profile %s (%s)", field_id, profile_id, profile_name)
    return changed


ALLOWED_QUICK_FIELD_SETTINGS = {"print_by_default", "always_use_for_qr"}


def update_profile_field_setting(profile_id: str, field_id: str, setting: str, value: bool) -> Dict:
    if setting not in ALLOWED_QUICK_FIELD_SETTINGS:
        raise ValueError(f"Unsupported field setting: {setting}")
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    field_store = load_field_store(profiles)
    fields = list(field_store.get(profile_id, []))
    for idx, field in enumerate(fields):
        if field.get("id") != field_id:
            continue
        updated = normalize_profile_field({**field, setting: value}, idx + 1)
        fields[idx] = updated
        field_store[profile_id] = fields
        save_field_store(field_store)
        LOGGER.info("Updated field setting %s=%s for %s in profile %s", setting, value, field_id, profile_id)
        return updated
    raise ValueError(f"Unknown field: {field_id}")


def render_page(form: Dict[str, object], opts: Dict, field_forms: List[Dict], result: Dict | None = None, field_result: Dict | None = None, editor_form: Dict | None = None) -> str:
    profile = opts.get("active_profile") or {}
    layout = effective_layout(profile)
    ui = get_ui_strings(opts.get("ui_language"))
    preview_display_width_mm = profile.get("label_width_mm", 170.0)
    preview_display_height_mm = profile.get("label_height_mm", 305.0)
    if profile.get("print_rotation_degrees") in (90, 270):
        preview_display_width_mm, preview_display_height_mm = preview_display_height_mm, preview_display_width_mm
    qr_selected_ids = normalize_qr_field_ids(form.get("qr_field_ids", []))
    qr_preview = qr_payload_from_field_forms(field_forms, qr_selected_ids) or ui["none"]
    qr_field_options = [
        {
            "id": field["id"],
            "name": field["name"],
            "value": normalize_qr_value(field.get("value", "")),
            "selected": field["id"] in qr_selected_ids,
        }
        for field in field_forms
    ]
    editor_form = editor_form or blank_editor_form()
    return render_template_string(
        HTML,
        ui=ui,
        result=result,
        field_result=field_result,
        form=form,
        field_forms=field_forms,
        active_profile_fields=profile.get("fields", []),
        label_profiles=opts.get("label_profiles", []),
        active_profile_id=opts.get("active_profile_id", ""),
        active_profile_name=opts.get("active_profile_name", ""),
        printer_host=profile.get("printer_host", ""),
        printer_port=profile.get("printer_port", ""),
        printer_target=format_printer_target(profile, opts),
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
        editor_form=editor_form,
        field_editor_json=field_store_map_for_profile(profile),
        qr_field_options=qr_field_options,
        qr_selected_ids=qr_selected_ids,
        alignments=sorted(ALIGNMENTS),
        font_families=sorted(FONT_FAMILIES),
        field_positions=["body", "footer"],
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
    field_result = None
    if opts.get("migration_notice"):
        field_result = {"success": True, "message": ui_text(opts, opts["migration_notice"])}
    return render_page(form, opts, field_forms, result=None, field_result=field_result)


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_runtime_options()
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    result = {"success": False, "message": ui_text(opts, "unknown_error")}
    try:
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        qr_value = qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
        copies = max(1, min(50, int(form.get("copies", "1"))))
        zpl = build_zpl(qr_value, field_forms, copies, profile)
        host, port = resolve_printer_target(profile, opts)
        LOGGER.info("Print request received: profile=%s copies=%s qr_payload=%r", profile.get("id"), copies, qr_value)
        send_to_printer(host, port, zpl)
        result = {"success": True, "message": ui_text(opts, "sent_labels_message", copies=copies, host=host, port=port, qr_payload=qr_value or ui_text(opts, "none"))}
    except Exception as exc:
        LOGGER.exception("Print failed")
        result = {"success": False, "message": ui_text(opts, "print_failed_message", error=exc)}
    return render_page(form, opts, field_forms, result=result)


@APP.route("/fields/save", methods=["POST"])
def save_field():
    opts = load_runtime_options(request.form.get("profile_id") or None)
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    editor_form = blank_editor_form()
    result = None
    try:
        if not profile:
            raise ValueError(ui_text(opts, "profile_not_found"))
        original_field_id, normalized_field = validate_and_normalize_editor_payload(request.form, opts["ui_language"])
        save_profile_field(profile["id"], original_field_id, normalized_field, profile.get("name", ""), opts["ui_language"])
        opts = load_runtime_options(profile["id"])
        form, field_forms = form_data_from_request(opts)
        result = {
            "success": True,
            "message": ui_text(opts, "field_saved_message", field=normalized_field["name"], profile=opts.get("active_profile_name", "")),
        }
        editor_form = blank_editor_form()
    except Exception as exc:
        LOGGER.exception("Field save failed")
        editor_form = editor_form_from_field({
            "id": request.form.get("id", ""),
            "name": request.form.get("name", ""),
            "default_value": request.form.get("default_value", ""),
            "alignment": request.form.get("alignment", "center"),
            "font_family": request.form.get("font_family", "sans"),
            "font_size_mm": request.form.get("font_size_mm", 7.0),
            "bold": normalize_bool(request.form.get("bold"), False),
            "italic": normalize_bool(request.form.get("italic"), False),
            "underline": normalize_bool(request.form.get("underline"), False),
            "print_by_default": normalize_bool(request.form.get("print_by_default"), True),
            "required": normalize_bool(request.form.get("required"), False),
            "number_only": normalize_bool(request.form.get("number_only"), False),
            "suffix": request.form.get("suffix", ""),
            "position": request.form.get("position", "body"),
            "footer_text": normalize_bool(request.form.get("footer_text"), False),
            "append_current_date": normalize_bool(request.form.get("append_current_date"), False),
            "always_use_for_qr": normalize_bool(request.form.get("always_use_for_qr"), False),
            "value_options": normalize_value_options(request.form.get("value_options_text", "")),
            "max_lines": request.form.get("max_lines", 3),
        })
        editor_form["original_field_id"] = request.form.get("original_field_id", "")
        result = {"success": False, "message": ui_text(opts, "field_save_failed", error=exc)}
    return render_page(form, opts, field_forms, field_result=result, editor_form=editor_form)


@APP.route("/fields/delete", methods=["POST"])
def delete_field():
    opts = load_runtime_options(request.form.get("profile_id") or None)
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    result = None
    try:
        if not profile:
            raise ValueError(ui_text(opts, "profile_not_found"))
        field_id = sanitize_id(str(request.form.get("field_id") or ""), "")
        deleted = delete_profile_field(profile["id"], field_id, profile.get("name", ""))
        opts = load_runtime_options(profile["id"])
        form, field_forms = form_data_from_request(opts)
        result = {
            "success": deleted,
            "message": ui_text(opts, "field_deleted_message", field=field_id, profile=opts.get("active_profile_name", "")) if deleted else ui_text(opts, "field_delete_failed", error=ui_text(opts, "profile_not_found") if not field_id else field_id),
        }
    except Exception as exc:
        LOGGER.exception("Field delete failed")
        result = {"success": False, "message": ui_text(opts, "field_delete_failed", error=exc)}
    return render_page(form, opts, field_forms, field_result=result)


@APP.route("/fields/quick-update", methods=["POST"])
def quick_update_field_setting():
    opts = load_runtime_options(request.form.get("profile_id") or None)
    profile = opts.get("active_profile") or {}
    try:
        if not profile:
            raise ValueError(ui_text(opts, "profile_not_found"))
        field_id = sanitize_id(str(request.form.get("field_id") or ""), "")
        setting = normalize_string(request.form.get("setting"), "")
        value = normalize_bool(request.form.get("value"), False)
        updated = update_profile_field_setting(profile["id"], field_id, setting, value)
        return jsonify({"ok": True, "field": updated, "profile_id": profile["id"]})
    except Exception as exc:
        LOGGER.exception("Quick field update failed")
        return jsonify({"ok": False, "error": str(exc)}), 400


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_runtime_options()
    profile = opts.get("active_profile") or {}
    form, field_forms = form_data_from_request(opts)
    try:
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        qr_value = qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
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
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        qr_value = qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
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
        field_forms = validate_field_forms(api_field_forms_from_payload(profile, payload), opts["ui_language"])
        qr_field_ids = selected_qr_field_ids_from_source(profile, payload)
        qr_value = qr_payload_from_field_forms(field_forms, qr_field_ids) if qr_field_ids else normalize_qr_value(payload.get("qr_value", profile.get("qr_default_value", "")))
        copies = max(1, min(50, int(payload.get("copies", 1))))
        zpl = build_zpl(qr_value, field_forms, copies, profile)
        host, port = resolve_printer_target(profile, opts)
        LOGGER.info("API print request received: profile=%s copies=%s qr_value=%r", profile.get("id"), copies, qr_value)
        send_to_printer(host, port, zpl)
        return jsonify({
            "ok": True,
            "profile_id": profile.get("id", ""),
            "printer": host,
            "printer_port": port,
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
