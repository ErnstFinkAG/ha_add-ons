import base64
import binascii
import json
import logging
import os
import re
import socket
import zlib
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from typing import Dict, List, Tuple
from uuid import uuid4
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

DEFAULT_PRINTER_DPI = 203
PRINTER_MAX_WIDTH_MM = 168.0
INGRESS_ALLOWED_IP = "172.30.32.2"
LOCAL_ALLOWED_IPS = {"127.0.0.1", "::1", None}
OPTIONS_PATH = "/data/options.json"
FIELD_STORE_PATH = "/data/label_fields.json"
ASSET_ROOT_PATH = "/data/field_assets"
LOGO_ASSET_PATH = os.path.join(ASSET_ROOT_PATH, "logos")
DEFAULT_TEXTBOX_MARGIN_LEFT_MM = 8.0
DEFAULT_TEXTBOX_MARGIN_RIGHT_MM = 8.0
DEFAULT_TEXTBOX_TOP_MARGIN_MM = 8.0
DEFAULT_LOGO_HEIGHT_MM = 20.0
DEFAULT_LOGO_GAP_MM = 4.0
FIELD_GAP_MM = 4.0
FOOTER_GAP_MM = 3.0
GROUPED_FIELD_GAP_MM = 1.0
DEFAULT_QR_TEXT_GAP_MM = 8.0
SUPPORTED_UI_LANGUAGES = {"en", "de"}
SUPPORTED_ROTATIONS = {0, 90, 270}
ALIGNMENTS = {"left", "center", "right"}
FONT_FAMILIES = {"sans", "serif", "mono"}
FIELD_POSITIONS = {"body", "footer"}

ALLOWED_QUICK_FIELD_SETTINGS = {"print_by_default", "always_use_for_qr"}

DEFAULT_OPTIONS = {
    "ui_language": "de",
    "show_global_field_config": True,
    "label_profiles": [],
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
        "intro_text": "Create label profiles in the add-on configuration. Fields are global and managed once here in the web UI for all label profiles.",
        "intro_text_fields_hidden": "Create label profiles in the add-on configuration. Global fields remain active, but their configuration is hidden in this web UI by the add-on settings.",
                "profile_none": "(none)",
        "qr_value_label": "QR fields",
        "qr_field_help": "Use the QR code checkbox on each field card. The QR content is built automatically from the current values of the selected fields.",
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
        "preview_meta": "PNG is rendered from the same layout coordinates used for print generation and exported at {dpi} dpi. Portrait preview tries to match the configured label size in mm. Horizontal preview keeps aspect ratio and fits to the available width. Red outlines show the QR footprint and the text box.",
        "fields_heading": "Configured fields",
        "print_field": "Print",
        "required": "Required",
        "numeric_only": "Numbers only",
        "position": "Position",
        "position_body": "Body",
        "position_footer": "Footer",
        "field_order_label": "Text block order",
        "field_order_help": "Lower numbers are rendered first inside the body or footer text block.",
        "configured_label_mapping": "Current label setup",
                "current_qr_payload": "Current QR payload",
        "requested_label": "Requested label",
        "requested_qr": "Requested QR",
        "effective_print_width": "Effective print width on ZT420/ZT421 @ {dpi} dpi",
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
        "field_manager_intro": "These global fields are shared by all label profiles. Add, edit, or delete them here without touching the add-on settings.",
        "save_field_button": "Save field",
        "new_field_button": "New field",
        "delete_field_button": "Delete",
        "edit_field_button": "Edit",
        "move_up_button": "Up",
        "move_down_button": "Down",
        "no_fields_configured": "No fields configured for this label yet.",
        "field_saved_message": "Field '{field}' saved.",
        "field_deleted_message": "Field '{field}' deleted.",
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
        "footer_bottom_margin_label": "Footer bottom margin (mm)",
        "footer_logo_text_gap_label": "Footer logo/text gap (mm)",
        "footer_logo_text_gap_help": "Gap between footer logos and footer text when both are rendered from the same field.",
        "value_options_label": "Value list",
        "value_options_help": "Optional suggestions, one value per line. Users can still enter any text.",
        "value_options_summary": "Choices",
        "logo_field_label": "Logo field",
        "logo_height_label": "Logo height (mm)",
        "logo_upload_label": "Upload PNG logo(s)",
        "logo_upload_help": "Upload one or more PNG files. They will be shown as selectable checkboxes in the label form.",
        "logo_options_summary": "Logos",
        "existing_logos_label": "Existing logos",
        "no_logos_uploaded": "No logos uploaded for this field yet.",
        "default_logo_label": "Selected by default",
        "logo_order_label": "Order",
        "logo_order_help": "Lower numbers come first and are placed further left.",
        "remove_logo_label": "Remove",
        "logo_png_only": "Only PNG logo files are supported.",
        "logo_upload_invalid": "Logo upload failed: {error}",
        "logo_choices_label": "Logos",
        "max_lines_label": "Max lines",
        "field_summary_default": "Default",
        "field_summary_style": "Style",
        "field_summary_behavior": "Behavior",
        "field_editor_hint": "IDs are sanitized automatically and must be unique within the selected label.",
        "field_duplicate_error": "Field ID '{field_id}' already exists in this profile.",
        "field_name_required": "Field name is required.",
        "profile_not_found": "Label profile not found.",
        "preview_profiles_label": "Profiles shown in preview",
        "no_preview_profiles": "No label profile has show_in_preview enabled.",
        "global_fields_tag": "Global fields",
        "legacy_migrated": "Legacy label_profiles_yaml was detected and migrated. Profiles now live in add-on settings, fields live in the web UI store.",
        "language_label": "Language",
        "profile_settings_source": "Profiles are defined in add-on settings",
        "printer_dpi": "Printer DPI",
        "print_in_progress": "Sending label to printer...",
        "text_box_margin_left_profile_label": "Text box left margin (mm)",
        "text_box_margin_right_profile_label": "Text box right margin (mm)",
        "no_profiles_configured": "No label profile configured yet. Create your first label profile in the add-on configuration and restart the add-on.",
        "fields_available_after_profile": "Global fields become available after at least one label profile exists.",
        "preview_image_print_hint": "Click the preview image to print this label with the selected copy count.",
        "field_manager_disabled_message": "Global field configuration is disabled in the add-on settings.",
    },
    "de": {
        "lang": "de",
        "page_title": "Inventory Label",
        "intro_text": "Lege die Etikettenprofile in der Add-on-Konfiguration an. Die Felder sind global und werden hier einmalig für alle Etikettenprofile verwaltet.",
        "intro_text_fields_hidden": "Lege die Etikettenprofile in der Add-on-Konfiguration an. Die globalen Felder bleiben aktiv, ihre Konfiguration ist in dieser Weboberfläche aber per Add-on-Einstellung ausgeblendet.",
                "profile_none": "(keins)",
        "qr_value_label": "QR-Felder",
        "qr_field_help": "Verwende die QR-Code-Checkbox in jeder Feldkarte. Der QR-Inhalt wird automatisch aus den aktuellen Werten der ausgewählten Felder zusammengesetzt.",
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
        "preview_meta": "Die PNG-Vorschau wird aus denselben Layout-Koordinaten wie der Druck erstellt und mit {dpi} dpi exportiert. Hochformat versucht die konfigurierte Labelgröße in mm abzubilden. Querformat behält das Seitenverhältnis bei und passt sich an die verfügbare Breite an. Rote Rahmen zeigen die QR-Fläche und den Textblock.",
        "fields_heading": "Konfigurierte Felder",
        "print_field": "Drucken",
        "required": "Pflichtfeld",
        "numeric_only": "Nur Zahlen",
        "position": "Position",
        "position_body": "Inhalt",
        "position_footer": "Footer",
        "field_order_label": "Reihenfolge im Textblock",
        "field_order_help": "Kleinere Zahlen werden innerhalb des Inhalts- oder Footer-Textblocks zuerst gerendert.",
        "configured_label_mapping": "Aktuelle Etikettenkonfiguration",
                "current_qr_payload": "Aktueller QR-Inhalt",
        "requested_label": "Gewünschtes Label",
        "requested_qr": "Gewünschter QR",
        "effective_print_width": "Effektive Druckbreite auf ZT420/ZT421 @ {dpi} dpi",
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
        "field_manager_intro": "Diese globalen Felder gelten für alle Etikettenprofile. Hier können sie hinzugefügt, bearbeitet oder gelöscht werden, ohne die Add-on-Einstellungen anzufassen.",
        "save_field_button": "Feld speichern",
        "new_field_button": "Neues Feld",
        "delete_field_button": "Löschen",
        "edit_field_button": "Bearbeiten",
        "move_up_button": "Hoch",
        "move_down_button": "Runter",
        "no_fields_configured": "Für dieses Label sind noch keine Felder konfiguriert.",
        "field_saved_message": "Feld '{field}' gespeichert.",
        "field_deleted_message": "Feld '{field}' gelöscht.",
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
        "footer_bottom_margin_label": "Footer-Abstand unten (mm)",
        "footer_logo_text_gap_label": "Abstand Logo/Text im Footer (mm)",
        "footer_logo_text_gap_help": "Abstand zwischen Footer-Logos und Footer-Text, wenn beides aus demselben Feld gerendert wird.",
        "value_options_label": "Werteliste",
        "value_options_help": "Optionale Vorschläge, ein Wert pro Zeile. Freitext bleibt weiterhin möglich.",
        "value_options_summary": "Auswahlwerte",
        "logo_field_label": "Logo-Feld",
        "logo_height_label": "Logo-Höhe (mm)",
        "logo_upload_label": "PNG-Logo(s) hochladen",
        "logo_upload_help": "Ein oder mehrere PNG-Dateien hochladen. Sie werden im Label-Formular als auswählbare Checkboxen angezeigt.",
        "logo_options_summary": "Logos",
        "existing_logos_label": "Vorhandene Logos",
        "no_logos_uploaded": "Für dieses Feld sind noch keine Logos hochgeladen.",
        "default_logo_label": "Standardmäßig ausgewählt",
        "logo_order_label": "Reihenfolge",
        "logo_order_help": "Kleinere Zahlen kommen zuerst und werden weiter links platziert.",
        "remove_logo_label": "Entfernen",
        "logo_png_only": "Es werden nur PNG-Logodateien unterstützt.",
        "logo_upload_invalid": "Logo-Upload fehlgeschlagen: {error}",
        "logo_choices_label": "Logos",
        "max_lines_label": "Max. Zeilen",
        "field_summary_default": "Standard",
        "field_summary_style": "Stil",
        "field_summary_behavior": "Verhalten",
        "field_editor_hint": "IDs werden automatisch bereinigt und müssen innerhalb des ausgewählten Labels eindeutig sein.",
        "field_duplicate_error": "Feld-ID '{field_id}' existiert in diesem Profil bereits.",
        "field_name_required": "Feldname ist erforderlich.",
        "profile_not_found": "Etikettenprofil nicht gefunden.",
        "preview_profiles_label": "Profile in der Vorschau",
        "no_preview_profiles": "Kein Etikettenprofil hat show_in_preview aktiviert.",
        "global_fields_tag": "Globale Felder",
        "legacy_migrated": "Altes label_profiles_yaml erkannt und migriert. Profile liegen jetzt in den Add-on-Einstellungen, Felder im Web-UI-Speicher.",
        "language_label": "Sprache",
        "profile_settings_source": "Profile werden in den Add-on-Einstellungen definiert",
        "printer_dpi": "Drucker-DPI",
        "print_in_progress": "Etikett wird an den Drucker gesendet...",
        "text_box_margin_left_profile_label": "Linker Textblock-Rand (mm)",
        "text_box_margin_right_profile_label": "Rechter Textblock-Rand (mm)",
        "no_profiles_configured": "Es ist noch kein Etikettenprofil konfiguriert. Erstelle zuerst ein Etikettenprofil in der Add-on-Konfiguration und starte das Add-on danach neu.",
        "fields_available_after_profile": "Globale Felder stehen erst zur Verfügung, wenn mindestens ein Etikettenprofil existiert.",
        "preview_image_print_hint": "Klicke auf das Vorschaubild, um dieses Etikett mit der gewählten Anzahl zu drucken.",
        "field_manager_disabled_message": "Die globale Feldkonfiguration ist in den Add-on-Einstellungen deaktiviert.",
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
    .wrap { max-width: 1400px; margin: 0 auto; padding: 24px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); margin-bottom: 20px; }
    h1, h2, h3 { margin-top: 0; }
    label { display: block; font-weight: 600; margin-bottom: 8px; }
    input, select, textarea { width: 100%; border-radius: 12px; border: 1px solid var(--border); background: #0f172a; color: var(--text); padding: 12px 14px; font: inherit; margin-bottom: 16px; }
    textarea { min-height: 110px; resize: vertical; }
    input[type="checkbox"] { width: auto; margin: 0; accent-color: var(--accent); }
    .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
    .row-compact { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
    .btns { display: flex; gap: 12px; flex-wrap: wrap; }
    button, .button-link { border: none; background: var(--accent); color: white; padding: 12px 18px; border-radius: 12px; font: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
    button.secondary, .button-link.secondary { background: transparent; border: 1px solid var(--border); }
    button.danger { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); }
    .flash { border-radius: 12px; padding: 14px 16px; margin-bottom: 16px; }
    .flash.ok { background: rgba(16,185,129,0.14); border: 1px solid var(--ok); }
    .flash.error { background: rgba(239,68,68,0.14); border: 1px solid var(--danger); }
    .muted { color: var(--muted); }
    .headline-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
    .top-layout { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr); gap: 20px; align-items: start; }
    .top-panel { background: #111827; border: 1px solid var(--border); border-radius: 16px; padding: 18px; }
    .fields-section { margin-top: 0; }
    .value-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .field-grid { display: grid; gap: 14px; }
    .field-card { background: #111827; border: 1px solid var(--border); border-radius: 14px; padding: 14px; }
    .field-card h3 { margin-bottom: 10px; font-size: 1rem; }
    .field-meta { display: flex; flex-wrap: wrap; gap: 12px; color: var(--muted); font-size: 0.92rem; margin-bottom: 12px; }
    .checkline { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
    .selector-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .selector-option { display: flex; gap: 10px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 14px; background: #111827; cursor: pointer; }
    .selector-option input { margin-top: 3px; margin-bottom: 0; }
    .selector-text { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .selector-text strong, .selector-text span { word-break: break-word; }
    .config-list { margin: 0; padding-left: 18px; color: var(--muted); }
    .config-list li + li { margin-top: 8px; }
    .preview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
    .preview-card { background: #111827; border: 1px solid var(--border); border-radius: 16px; padding: 16px; }
    .preview-frame { width: 100%; min-height: 240px; background: var(--label-bg); border: 1px solid var(--label-edge); border-radius: 12px; overflow: hidden; }
    .preview-image-button { display: block; width: 100%; padding: 0; border: none; background: transparent; cursor: pointer; }
    .preview-frame img { display: block; width: 100%; height: auto; background: white; }
    .preview-meta { margin-top: 12px; font-size: 0.95rem; color: var(--muted); }
    .preview-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    .copies-inline { width: 130px; }
    .copies-inline label { margin-bottom: 6px; }
    .copies-inline input { margin-bottom: 0; }
    .tag-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .tag { display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px; background: #0f172a; border: 1px solid var(--border); color: var(--muted); font-size: 0.9rem; }
    .field-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    .small { font-size: 0.92rem; }
    .editor { background: #111827; border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
    .logo-option-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; }
    .logo-option-card { display: flex; flex-direction: column; gap: 8px; align-items: center; text-align: center; border: 1px solid var(--border); border-radius: 12px; padding: 10px; background: #0f172a; }
    .logo-option-card img, .logo-thumb { max-width: 100%; max-height: 70px; object-fit: contain; background: white; border-radius: 8px; padding: 4px; box-sizing: border-box; }
    .logo-manager { display: grid; gap: 10px; }
    .logo-manager-item { display: grid; grid-template-columns: 90px 1fr 120px auto; gap: 12px; align-items: center; border: 1px solid var(--border); border-radius: 12px; padding: 10px; background: #111827; }
    .logo-order-input { margin: 0; }
    code { word-break: break-word; }
    @media (max-width: 980px) {
      .top-layout, .value-field-grid { grid-template-columns: 1fr; }
      .logo-manager-item { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div id="print-result-container">
      {% if result %}
        <div class="flash {{ 'ok' if result.success else 'error' }}">{{ result.message }}</div>
      {% endif %}
      {% if field_result %}
        <div class="flash {{ 'ok' if field_result.success else 'error' }}">{{ field_result.message }}</div>
      {% endif %}
      </div>
      <form id="label-form" method="post">
        <div class="top-layout">
          <div class="top-panel">
            <div class="headline-row">
              <div>
                <h1>{{ ui.page_title }}</h1>
                <p class="muted">{{ ui.intro_text }}</p>
              </div>
            </div>

            <div class="row-compact">
              <div class="copies-inline">
                <label for="copies">{{ ui.copies }}</label>
                <input id="copies" name="copies" type="number" min="1" max="50" value="{{ form.copies }}" required>
              </div>
              <div>
                <label>{{ ui.preview_profiles_label }}</label>
                <input value="{{ preview_profile_names_text }}" disabled>
              </div>
            </div>
            {% if not has_profiles %}
            <div class="flash error">{{ ui.no_profiles_configured }}</div>
            <p class="muted small">{{ ui.fields_available_after_profile }}</p>
            {% endif %}

            <div class="fields-section">
              <h2>{{ ui.fields_heading }}</h2>
              <div class="value-field-grid">
                {% for field in field_forms %}
                <div class="field-card">
                  <h3>{{ field.name }}</h3>
                  <div class="field-meta">
                    <span>{{ ui.position }}: {{ ui.position_footer if field.position == 'footer' else ui.position_body }}</span>
                    {% if field.required %}<span>{{ ui.required }}</span>{% endif %}
                    {% if field.number_only %}<span>{{ ui.numeric_only }}</span>{% endif %}
                    {% if field.supports_logos %}<span>{{ ui.logo_field_label }}</span>{% endif %}
                  </div>
                  <div class="row-compact" style="margin-bottom:12px;">
                    <div class="checkline" style="margin-bottom:0;">
                      <input id="print_{{ field.id }}" name="print_{{ field.id }}" type="checkbox" value="1" data-field-id="{{ field.id }}" {% if field.print_enabled %}checked{% endif %}>
                      <label for="print_{{ field.id }}" style="margin:0; font-weight:500;">{{ ui.print_field }}</label>
                    </div>
                    {% if field.supports_text %}
                    <div class="checkline" style="margin-bottom:0;">
                      <input id="qr_field_{{ field.id }}" name="qr_field_ids" type="checkbox" value="{{ field.id }}" data-field-id="{{ field.id }}" {% if field.id in qr_selected_ids %}checked{% endif %}>
                      <label for="qr_field_{{ field.id }}" style="margin:0; font-weight:500;">{{ ui.always_use_for_qr_label }}</label>
                    </div>
                    {% endif %}
                  </div>
                  {% if field.supports_logos %}
                  <input type="hidden" name="field_{{ field.id }}__logos_present" value="1">
                  <div class="logo-option-grid">
                    {% for option in field.logo_options %}
                    <label class="logo-option-card">
                      <input type="checkbox" name="field_{{ field.id }}__logos" value="{{ option.id }}" {% if option.selected %}checked{% endif %}>
                      <img src="{{ option.asset_url }}" alt="{{ option.name }}">
                    </label>
                    {% else %}
                    <div class="muted small">{{ ui.no_logos_uploaded }}</div>
                    {% endfor %}
                  </div>
                  {% endif %}
                  {% if field.supports_text %}
                  <input id="field_{{ field.id }}" name="field_{{ field.id }}" type="text" value="{{ field.value }}" {% if field.value_options %}list="field_options_{{ field.id }}"{% endif %} {% if field.number_only %}inputmode="numeric" pattern="[0-9]*" data-number-only="1"{% endif %}>
                  {% if field.value_options %}
                  <datalist id="field_options_{{ field.id }}">
                    {% for option in field.value_options %}
                    <option value="{{ option }}"></option>
                    {% endfor %}
                  </datalist>
                  {% endif %}
                  {% endif %}
                </div>
                {% else %}
                <div class="field-card muted">{{ ui.no_fields_configured }}</div>
                {% endfor %}
              </div>
            </div>

            <p class="muted small">{{ ui.qr_field_help }}</p>
            {% if not qr_selected_ids %}
            <p class="muted small">{{ ui.qr_field_empty }}</p>
            {% endif %}

          </div>

          <div class="top-panel">
            <h2>{{ ui.preview_heading }}</h2>
            {% if preview_profiles %}
            <div class="preview-grid">
              {% for profile in preview_profiles %}
              <div class="preview-card preview-card-js" data-profile-id="{{ profile.id }}">
                <div class="headline-row">
                  <div>
                    <h3 style="margin-bottom:4px;">{{ profile.name }}</h3>
                    <div class="muted small">{{ profile.printer_target }}</div>
                  </div>
                  <div class="tag-list">
                    <span class="tag">{{ profile.label_width_mm }} × {{ profile.label_height_mm }} mm</span>
                    <span class="tag">{{ ui.printer_dpi }}: {{ profile.printer_dpi }}</span>
                    <span class="tag">{{ ui.text_box_margin_left_profile_label }}: {{ profile.text_box_margin_left_mm }} mm</span>
                    <span class="tag">{{ ui.text_box_margin_right_profile_label }}: {{ profile.text_box_margin_right_mm }} mm</span>
                    <span class="tag">{{ ui.print_rotation }}: {{ profile.print_rotation_degrees }}°</span>
                  </div>
                </div>
                <div class="preview-frame">
                  <button type="submit" class="preview-image-button js-print-trigger" data-profile-id="{{ profile.id }}" formaction="{{ ingress_base }}/print?profile_id={{ profile.id }}" title="{{ ui.preview_image_print_hint }}">
                    <img class="preview-image" src="{{ ingress_base }}/preview.png?{{ profile.preview_query }}" alt="{{ ui.preview_alt }}">
                  </button>
                </div>
                <div class="preview-meta">{{ ui.preview_image_print_hint }}</div>
                <div class="preview-actions">
                  <button type="submit" class="js-print-trigger" data-profile-id="{{ profile.id }}" formaction="{{ ingress_base }}/print?profile_id={{ profile.id }}">{{ ui.print_label_button }}</button>
                  <a class="button-link secondary preview-zpl-link" href="{{ ingress_base }}/preview?{{ profile.preview_query }}">{{ ui.preview_zpl }}</a>
                  <a class="button-link secondary preview-png-link" href="{{ ingress_base }}/preview.png?{{ profile.preview_query }}" target="_blank" rel="noopener">{{ ui.open_png_preview }}</a>
                </div>
              </div>
              {% endfor %}
            </div>
            {% else %}
            <p class="muted">{{ ui.no_preview_profiles }}</p>
            {% endif %}
          </div>
        </div>

        <div class="top-panel" style="margin-top:18px;">
          <h2>{{ ui.configured_label_mapping }}</h2>
          <ul class="config-list">
            <li><strong>{{ ui.current_qr_payload }}:</strong> <code>{{ qr_preview or ui.none }}</code></li>
            <li><strong>{{ ui.preview_profiles_label }}:</strong> <code>{{ preview_profile_names_text or ui.none }}</code></li>
            <li><strong>{{ ui.language_label }}:</strong> <code>{{ ui.lang }}</code></li>
          </ul>
        </div>
      </form>
    </div>

    {% if show_global_field_config %}
    <div class="card">
      <div class="headline-row">
        <div>
          <h2>{{ ui.field_manager_heading }}</h2>
          <p class="muted">{{ ui.field_manager_intro }}</p>
        </div>
        <span class="tag">{{ ui.global_fields_tag }}</span>
      </div>

      {% if has_profiles %}
      <div class="field-grid">
        {% for field in configured_fields %}
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
            {% if field.footer_bottom_margin_mm %}<span class="tag">{{ ui.footer_bottom_margin_label }}: {{ field.footer_bottom_margin_mm }} mm</span>{% endif %}
            {% if field.position == 'footer' and field.supports_logos and field.footer_text %}<span class="tag">{{ ui.footer_logo_text_gap_label }}: {{ field.footer_logo_text_gap_mm }} mm</span>{% endif %}
            {% if field.value_options %}<span class="tag">{{ ui.value_options_summary }}: {{ field.value_options|length }}</span>{% endif %}
            {% if field.supports_logos %}<span class="tag">{{ ui.logo_field_label }}</span>{% endif %}
            {% if field.logo_options %}<span class="tag">{{ ui.logo_options_summary }}: {{ field.logo_options|length }}</span>{% endif %}
            {% if field.append_current_date %}<span class="tag">Date</span>{% endif %}
          </div>
          {% if field.logo_options %}
          <div class="logo-option-grid" style="margin-bottom:12px;">
            {% for option in field.logo_options %}
            <div class="logo-option-card">
              <img src="{{ option.asset_url }}" alt="{{ option.name }}">
            </div>
            {% endfor %}
          </div>
          {% endif %}
          <div class="field-actions">
            <form method="post" action="{{ ingress_base }}/fields/move" style="margin:0;">
              <input type="hidden" name="field_id" value="{{ field.id }}">
              <input type="hidden" name="direction" value="up">
              <button type="submit" class="secondary" {% if not field.can_move_up %}disabled{% endif %}>{{ ui.move_up_button }}</button>
            </form>
            <form method="post" action="{{ ingress_base }}/fields/move" style="margin:0;">
              <input type="hidden" name="field_id" value="{{ field.id }}">
              <input type="hidden" name="direction" value="down">
              <button type="submit" class="secondary" {% if not field.can_move_down %}disabled{% endif %}>{{ ui.move_down_button }}</button>
            </form>
            <button type="button" class="secondary edit-field-button" data-field-id="{{ field.id }}">{{ ui.edit_field_button }}</button>
            <form method="post" action="{{ ingress_base }}/fields/delete" style="margin:0;">
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
        <form id="field-editor-form" method="post" action="{{ ingress_base }}/fields/save" enctype="multipart/form-data">
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
            <label class="checkline"><input id="editor_logo_field" name="logo_field" type="checkbox" value="1" {% if editor_form.logo_field %}checked{% endif %}> {{ ui.logo_field_label }}</label>
            <div>
              <label for="editor_logo_height_mm">{{ ui.logo_height_label }}</label>
              <input id="editor_logo_height_mm" name="logo_height_mm" type="number" min="2" max="100" step="0.5" value="{{ editor_form.logo_height_mm }}">
            </div>
          </div>

          <div>
            <label for="editor_logo_files">{{ ui.logo_upload_label }}</label>
            <input id="editor_logo_files" name="logo_files" type="file" accept=".png,image/png" multiple>
            <p class="muted small">{{ ui.logo_upload_help }}</p>
          </div>

          <div>
            <label>{{ ui.existing_logos_label }}</label>
            <div id="existing-logos" class="logo-manager">
              {% if editor_form.logo_options %}
                {% for option in editor_form.logo_options %}
                <label class="logo-manager-item">
                  <img class="logo-thumb" src="{{ option.asset_url }}" alt="{{ option.name }}">
                  <span>
                    <strong>{{ option.name }}</strong><br>
                    <span class="checkline"><input type="checkbox" name="default_logo_ids" value="{{ option.id }}" {% if option.selected_default %}checked{% endif %}> {{ ui.default_logo_label }}</span>
                  </span>
                  <span>
                    <label for="logo_order_{{ option.id }}">{{ ui.logo_order_label }}</label>
                    <input class="logo-order-input" id="logo_order_{{ option.id }}" name="logo_order_{{ option.id }}" type="number" min="1" step="1" value="{{ option.sort_order or loop.index }}">
                  </span>
                  <span class="checkline"><input type="checkbox" name="remove_logo_ids" value="{{ option.id }}"> {{ ui.remove_logo_label }}</span>
                </label>
                {% endfor %}
              {% else %}
                <div class="muted small">{{ ui.no_logos_uploaded }}</div>
              {% endif %}
            </div>
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
            <input id="editor_sort_order" name="sort_order" type="hidden" value="{{ editor_form.sort_order }}">
            <div>
              <label for="editor_max_lines">{{ ui.max_lines_label }}</label>
              <input id="editor_max_lines" name="max_lines" type="number" min="1" max="8" step="1" value="{{ editor_form.max_lines }}">
            </div>
            <div>
              <label for="editor_footer_bottom_margin_mm">{{ ui.footer_bottom_margin_label }}</label>
              <input id="editor_footer_bottom_margin_mm" name="footer_bottom_margin_mm" type="number" min="0" max="100" step="0.5" value="{{ editor_form.footer_bottom_margin_mm }}">
            </div>
            <div>
              <label for="editor_footer_logo_text_gap_mm">{{ ui.footer_logo_text_gap_label }}</label>
              <input id="editor_footer_logo_text_gap_mm" name="footer_logo_text_gap_mm" type="number" min="0" max="50" step="0.5" value="{{ editor_form.footer_logo_text_gap_mm }}">
              <p class="muted small">{{ ui.footer_logo_text_gap_help }}</p>
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
      {% else %}
      <div class="field-card muted">{{ ui.fields_available_after_profile }}</div>
      {% endif %}
    </div>
    {% endif %}
  </div>

  <script>
    (function () {
      const form = document.getElementById("label-form");
      const previewCards = Array.from(document.querySelectorAll(".preview-card-js"));
      const newFieldButton = document.getElementById("new-field-button");
      const fieldEditorForm = document.getElementById("field-editor-form");
      const fieldData = {{ field_editor_json|tojson }};
      const ingressBase = {{ ingress_base|tojson }};
      const noLogosUploadedText = {{ ui.no_logos_uploaded|tojson }};
      const defaultLogoLabelText = {{ ui.default_logo_label|tojson }};
      const initialNextFieldSortOrder = {{ next_field_sort_order|tojson }};
      const logoOrderLabelText = {{ ui.logo_order_label|tojson }};
      const removeLogoLabelText = {{ ui.remove_logo_label|tojson }};
      if (!form) return;

      let refreshTimer = null;
      let previewNonce = Date.now();

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

      function buildQuery(profileId) {
        const params = new URLSearchParams();
        const formData = new FormData(form);
        for (const [key, value] of formData.entries()) {
          params.append(key, String(value));
        }
        params.set("copies", normalizedCopies());
        if (profileId) params.set("profile_id", profileId);
        return params;
      }

      function applyPreviewUpdate() {
        previewNonce += 1;
        previewCards.forEach((card) => {
          const profileId = card.getAttribute("data-profile-id") || "";
          const params = buildQuery(profileId);
          const pngParams = new URLSearchParams(params);
          pngParams.set("_", String(previewNonce));
          const img = card.querySelector(".preview-image");
          const pngLink = card.querySelector(".preview-png-link");
          const zplLink = card.querySelector(".preview-zpl-link");
          if (img) img.src = `${ingressBase}/preview.png?${pngParams.toString()}`;
          if (pngLink) pngLink.href = `${ingressBase}/preview.png?${params.toString()}`;
          if (zplLink) zplLink.href = `${ingressBase}/preview?${params.toString()}`;
        });
      }

      function schedulePreviewUpdate() {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(applyPreviewUpdate, 180);
      }

      function persistFieldCheckbox(fieldId, settingKey, checked) {
        if (!fieldId || !settingKey) return;
        const body = new URLSearchParams();
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

      function renderExistingLogos(logos) {
        const container = document.getElementById("existing-logos");
        if (!container) return;
        const items = Array.isArray(logos) ? logos : [];
        if (!items.length) {
          container.innerHTML = `<div class="muted small">${noLogosUploadedText}</div>`;
          return;
        }
        container.innerHTML = items.map((logo, index) => `
          <label class="logo-manager-item">
            <img class="logo-thumb" src="${logo.asset_url || ''}" alt="${logo.name || ''}">
            <span>
              <strong>${logo.name || ''}</strong><br>
              <span class="checkline"><input type="checkbox" name="default_logo_ids" value="${logo.id || ''}" ${logo.selected_default ? 'checked' : ''}> ${defaultLogoLabelText}</span>
            </span>
            <span>
              <label for="logo_order_${logo.id || ''}">${logoOrderLabelText}</label>
              <input class="logo-order-input" id="logo_order_${logo.id || ''}" name="logo_order_${logo.id || ''}" type="number" min="1" step="1" value="${logo.sort_order || (index + 1)}">
            </span>
            <span class="checkline"><input type="checkbox" name="remove_logo_ids" value="${logo.id || ''}"> ${removeLogoLabelText}</span>
          </label>
        `).join("");
      }

      function nextFieldSortOrder() {
        const values = Object.values(fieldData || {}).map((item) => {
          const parsed = parseInt(item && item.sort_order != null ? item.sort_order : "", 10);
          return Number.isNaN(parsed) ? 0 : parsed;
        });
        const currentMax = values.length ? Math.max(...values) : 0;
        return Math.max(initialNextFieldSortOrder || 1, currentMax + 1);
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
        setValue("editor_sort_order", String(nextFieldSortOrder()));
        setValue("editor_max_lines", "3");
        setValue("editor_footer_bottom_margin_mm", "0.0");
        setValue("editor_footer_logo_text_gap_mm", "1.0");
        setValue("editor_value_options_text", "");
        setValue("editor_logo_height_mm", "20.0");
        setCheckbox("editor_bold", false);
        setCheckbox("editor_italic", false);
        setCheckbox("editor_underline", false);
        setCheckbox("editor_print_by_default", true);
        setCheckbox("editor_required", false);
        setCheckbox("editor_number_only", false);
        setCheckbox("editor_append_current_date", false);
        setCheckbox("editor_always_use_for_qr", false);
        setCheckbox("editor_footer_text", false);
        setCheckbox("editor_logo_field", false);
        const logoFilesInput = document.getElementById("editor_logo_files");
        if (logoFilesInput) logoFilesInput.value = "";
        renderExistingLogos([]);
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
        setValue("editor_sort_order", data.sort_order || nextFieldSortOrder());
        setValue("editor_max_lines", data.max_lines || "3");
        setValue("editor_footer_bottom_margin_mm", data.footer_bottom_margin_mm ?? "0.0");
        setValue("editor_footer_logo_text_gap_mm", data.footer_logo_text_gap_mm ?? "1.0");
        setValue("editor_value_options_text", data.value_options_text || "");
        setValue("editor_logo_height_mm", data.logo_height_mm || "20.0");
        setCheckbox("editor_bold", data.bold);
        setCheckbox("editor_italic", data.italic);
        setCheckbox("editor_underline", data.underline);
        setCheckbox("editor_print_by_default", data.print_by_default);
        setCheckbox("editor_required", data.required);
        setCheckbox("editor_number_only", data.number_only);
        setCheckbox("editor_append_current_date", data.append_current_date);
        setCheckbox("editor_always_use_for_qr", data.always_use_for_qr);
        setCheckbox("editor_footer_text", data.footer_text);
        setCheckbox("editor_logo_field", data.logo_field);
        const logoFilesInput = document.getElementById("editor_logo_files");
        if (logoFilesInput) logoFilesInput.value = "";
        renderExistingLogos(data.logo_options || []);
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

      const printResultContainer = document.getElementById("print-result-container");
      const printButtons = Array.from(document.querySelectorAll(".js-print-trigger"));
      const printInProgressText = {{ ui.print_in_progress|tojson }};

      function setPrintButtonsDisabled(disabled) {
        printButtons.forEach((button) => { button.disabled = !!disabled; });
      }

      function showPrintFlash(success, message) {
        if (!printResultContainer) return;
        printResultContainer.innerHTML = `<div class="flash ${success ? "ok" : "error"}">${message || ""}</div>`;
      }

      function printProfile(profileId) {
        if (!profileId) return;
        const body = buildQuery(profileId);
        setPrintButtonsDisabled(true);
        showPrintFlash(true, printInProgressText);
        fetch(`${ingressBase}/print?profile_id=${encodeURIComponent(profileId)}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept": "application/json",
            "X-Requested-With": "fetch",
          },
          body: body.toString(),
        }).then(async (response) => {
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || !payload) throw new Error((payload && payload.message) || `HTTP ${response.status}`);
          showPrintFlash(!!payload.success, payload.message || "");
        }).catch((error) => {
          showPrintFlash(false, error && error.message ? error.message : "Print failed");
        }).finally(() => {
          setPrintButtonsDisabled(false);
        });
      }

      printButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
          event.preventDefault();
          printProfile(button.getAttribute("data-profile-id") || "");
        });
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


def field_sort_order_key(field: object, default: int = 9999) -> int:
    if isinstance(field, dict):
        return normalize_int(field.get("sort_order"), default, 1, 9999)
    return default


def sort_fields_by_order(fields: List[Dict]) -> List[Dict]:
    enumerated = list(enumerate(fields))
    enumerated.sort(key=lambda item: (field_sort_order_key(item[1], item[0] + 1), item[0], str(item[1].get("name") or "").lower(), str(item[1].get("id") or "")))
    return [dict(item[1]) for item in enumerated]


def ensure_asset_directories() -> None:
    os.makedirs(LOGO_ASSET_PATH, exist_ok=True)


def sanitize_storage_name(value: object) -> str:
    filename = os.path.basename(str(value or "").strip())
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    if not filename.lower().endswith('.png'):
        return ''
    return filename


def logo_asset_path(storage_name: object) -> str:
    return os.path.join(LOGO_ASSET_PATH, sanitize_storage_name(storage_name))


def logo_asset_url(storage_name: object) -> str:
    safe_name = sanitize_storage_name(storage_name)
    if not safe_name:
        return ''
    return f"{ingress_base_path()}/assets/logos/{safe_name}"


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
    logo_field = normalize_bool(data.get("logo_field"), False)
    if footer_text:
        position = "footer"
    raw_default_value = data.get("default_value", "")
    raw_default_logo_ids = data.get("default_logo_ids")
    if logo_field and raw_default_logo_ids is None and isinstance(raw_default_value, (list, tuple, set)):
        raw_default_logo_ids = raw_default_value
        raw_default_value = ""
    default_value = raw_default_value if raw_default_value is not None else ""
    return {
        "id": field_id,
        "name": name,
        "default_value": str(default_value),
        "default_logo_ids": normalize_multi_value_ids(raw_default_logo_ids or []),
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
        "sort_order": normalize_int(data.get("sort_order", data.get("field_order", idx)), idx, 1, 9999),
        "footer_text": footer_text,
        "footer_bottom_margin_mm": normalize_float(data.get("footer_bottom_margin_mm"), 0.0, 0.0, 100.0),
        "footer_logo_text_gap_mm": normalize_float(data.get("footer_logo_text_gap_mm"), GROUPED_FIELD_GAP_MM, 0.0, 50.0),
        "append_current_date": normalize_bool(data.get("append_current_date"), False),
        "always_use_for_qr": normalize_bool(data.get("always_use_for_qr"), False),
        "value_options": normalize_value_options(data.get("value_options")),
        "logo_field": logo_field,
        "logo_height_mm": normalize_float(data.get("logo_height_mm"), DEFAULT_LOGO_HEIGHT_MM, 2.0, 100.0),
        "logo_options": normalize_logo_options(data.get("logo_options")),
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
        "printer_dpi": normalize_int(data.get("printer_dpi"), DEFAULT_PRINTER_DPI, 100, 1200),
        "label_width_mm": normalize_float(data.get("label_width_mm"), 170.0, 50.0, 500.0),
        "label_height_mm": normalize_float(data.get("label_height_mm"), 305.0, 50.0, 1000.0),
        "qr_size_mm": normalize_float(data.get("qr_size_mm"), 170.0, 0.0, 300.0),
        "top_margin_mm": normalize_float(data.get("top_margin_mm"), 0.0, 0.0, 100.0),
        "text_box_margin_left_mm": normalize_float(data.get("text_box_margin_left_mm"), DEFAULT_TEXTBOX_MARGIN_LEFT_MM, 0.0, 100.0),
        "text_box_margin_right_mm": normalize_float(data.get("text_box_margin_right_mm"), DEFAULT_TEXTBOX_MARGIN_RIGHT_MM, 0.0, 100.0),
        "footer_bottom_margin_mm": normalize_float(data.get("footer_bottom_margin_mm"), 0.0, 0.0, 50.0),
        "print_shift_x_mm": normalize_float(data.get("print_shift_x_mm"), 0.0, -100.0, 100.0),
        "print_shift_y_mm": normalize_float(data.get("print_shift_y_mm"), 0.0, -100.0, 100.0),
        "print_rotation_degrees": normalize_rotation_degrees(data.get("print_rotation_degrees"), 0),
        "qr_default_value": "" if data.get("qr_default_value") is None else str(data.get("qr_default_value")),
        "qr_quiet_zone_modules": normalize_int(data.get("qr_quiet_zone_modules"), 3, 0, 20),
        "qr_error_correction": str(data.get("qr_error_correction") or "M").strip().upper() if str(data.get("qr_error_correction") or "M").strip().upper() in QR_ERROR_CORRECTION_MAP else "M",
        "show_in_preview": normalize_bool(data.get("show_in_preview"), False),
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
    options["show_global_field_config"] = normalize_bool(raw.get("show_global_field_config"), DEFAULT_OPTIONS["show_global_field_config"])

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

    options["label_profiles"] = profiles
    return options, legacy_field_store, migrated_notice


def merge_field_lists(*field_lists: object) -> List[Dict]:
    merged: List[Dict] = []
    seen = set()
    for field_list in field_lists:
        if isinstance(field_list, dict):
            iterable = []
            for nested in field_list.values():
                if isinstance(nested, list):
                    iterable.extend(nested)
        elif isinstance(field_list, list):
            iterable = field_list
        else:
            iterable = []
        for item in iterable:
            if not isinstance(item, dict):
                continue
            field = normalize_profile_field(item, len(merged) + 1)
            field_id = field.get("id")
            if not field_id or field_id in seen:
                continue
            seen.add(field_id)
            merged.append(field)
    return sort_fields_by_order(merged)


def load_field_store(profiles: List[Dict], legacy_seed: Dict[str, List[Dict]] | None = None) -> List[Dict]:
    store: List[Dict] = []
    wrote_file = False
    if os.path.exists(FIELD_STORE_PATH):
        try:
            with open(FIELD_STORE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            store = merge_field_lists(data)
            wrote_file = isinstance(data, dict)
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", FIELD_STORE_PATH, exc)

    if not store and legacy_seed:
        store = merge_field_lists(legacy_seed)
        wrote_file = True

    if profiles and (wrote_file or not os.path.exists(FIELD_STORE_PATH)):
        save_field_store(store)
    return store


def save_field_store(store: List[Dict]) -> None:
    ordered = sort_fields_by_order([normalize_profile_field(field, idx) for idx, field in enumerate(store, start=1)])
    serializable = [normalize_profile_field(field, idx) for idx, field in enumerate(ordered, start=1)]
    with open(FIELD_STORE_PATH, "w", encoding="utf-8") as handle:
        json.dump(serializable, handle, ensure_ascii=False, indent=2)


def select_profile(profiles: List[Dict], requested_profile_id: str | None = None) -> Dict | None:
    selected_id = requested_profile_id or request.values.get("profile_id") or request.args.get("profile_id") or request.form.get("profile_id")
    if selected_id:
        match = next((profile for profile in profiles if profile["id"] == selected_id), None)
        if match is not None:
            return match
    preview_profiles = [profile for profile in profiles if profile.get("show_in_preview")]
    if preview_profiles:
        return preview_profiles[0]
    return profiles[0] if profiles else None


def load_runtime_options(profile_id: str | None = None) -> Dict:
    opts, legacy_seed, migration_notice = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    global_fields = load_field_store(profiles, legacy_seed)
    preview_profiles = [dict(profile) for profile in profiles if profile.get("show_in_preview")]
    requested_profile = select_profile(profiles, profile_id)

    opts["label_profiles"] = [dict(profile) for profile in profiles]
    opts["fields"] = deepcopy(global_fields)
    opts["preview_profiles"] = preview_profiles
    opts["requested_profile"] = dict(requested_profile) if requested_profile else None
    opts["requested_profile_id"] = requested_profile["id"] if requested_profile else ""
    opts["requested_profile_name"] = requested_profile["name"] if requested_profile else ""
    opts["migration_notice"] = migration_notice
    return opts


def field_value_name(field_id: str) -> str:
    return f"field_{field_id}"


def field_logo_value_name(field_id: str) -> str:
    return f"field_{field_id}__logos"


def field_logo_present_name(field_id: str) -> str:
    return f"field_{field_id}__logos_present"


def field_print_name(field_id: str) -> str:
    return f"print_{field_id}"


def field_supports_logos(field: Dict) -> bool:
    return normalize_bool(field.get("logo_field"), False) or bool(normalize_logo_options(field.get("logo_options", [])))


def field_supports_text(field: Dict) -> bool:
    return (not field_supports_logos(field)) or normalize_position(field.get("position"), "body") == "footer"


def build_field_forms(fields: List[Dict], source: Dict | None = None) -> List[Dict]:
    source = source or {}
    forms: List[Dict] = []
    for field in sort_fields_by_order(fields):
        value_key = field_value_name(field["id"])
        logo_value_key = field_logo_value_name(field["id"])
        logo_present_key = field_logo_present_name(field["id"])
        print_key = field_print_name(field["id"])
        supports_logos = field_supports_logos(field)
        supports_text = field_supports_text(field)
        print_raw = source.get(print_key)
        print_enabled = field["print_by_default"] if print_raw is None else normalize_bool(print_raw, field["print_by_default"])

        value = ""
        if supports_text:
            value = source.get(value_key)
            if value is None:
                value = field.get("default_value", "")
            value = str(value)

        selected_values: List[str] = []
        logo_options = [{**option, "asset_url": logo_asset_url(option.get("storage_name"))} for option in normalize_logo_options(field.get("logo_options", []))]
        if supports_logos:
            if hasattr(source, "getlist"):
                has_submission = source.get(logo_present_key) is not None or logo_value_key in source
                selected_values = normalize_multi_value_ids(source.getlist(logo_value_key)) if has_submission else normalize_multi_value_ids(field.get("default_logo_ids", []))
            else:
                has_submission = isinstance(source, dict) and (logo_present_key in source or logo_value_key in source)
                selected_values = normalize_multi_value_ids(source.get(logo_value_key, [])) if has_submission else normalize_multi_value_ids(field.get("default_logo_ids", []))
            selected_lookup = set(selected_values)
            logo_options = [{**option, "selected": option.get("id") in selected_lookup, "asset_url": option.get("asset_url") or logo_asset_url(option.get("storage_name"))} for option in logo_options]

        forms.append({
            **field,
            "supports_logos": supports_logos,
            "supports_text": supports_text,
            "value": value,
            "selected_logo_ids": selected_values,
            "logo_options": logo_options,
            "print_enabled": print_enabled,
        })
    return forms


def default_form_from_fields(fields: List[Dict]) -> Dict[str, object]:
    form: Dict[str, object] = {
        "qr_field_ids": [],
        "copies": "1",
    }
    for field in fields:
        if field_supports_text(field):
            form[field_value_name(field["id"])] = field.get("default_value", "")
        if field_supports_logos(field):
            form[field_logo_value_name(field["id"])] = normalize_multi_value_ids(field.get("default_logo_ids", []))
        if field["print_by_default"]:
            form[field_print_name(field["id"])] = "1"
    return form


def form_data_from_request(opts: Dict) -> Tuple[Dict[str, object], List[Dict]]:
    fields = opts.get("fields") or []
    defaults = default_form_from_fields(fields)
    selected_qr_fields = selected_qr_field_ids_from_source(fields, request.values)
    form: Dict[str, object] = {
        "qr_field_ids": selected_qr_fields,
        "copies": request.values.get("copies", defaults.get("copies", "1")),
    }
    field_forms = build_field_forms(fields, request.values)
    for field in field_forms:
        if field.get("supports_text"):
            form[field_value_name(field["id"])] = field["value"]
        if field.get("supports_logos"):
            form[field_logo_value_name(field["id"])] = normalize_multi_value_ids(field.get("selected_logo_ids", []))
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


def normalize_logo_options(value: object) -> List[Dict[str, str]]:
    raw_items = value if isinstance(value, list) else []
    result: List[Dict[str, str]] = []
    seen = set()
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            storage_name = sanitize_storage_name(item.get("storage_name") or item.get("filename") or item.get("path"))
            if not storage_name:
                continue
            option_id_base = sanitize_id(str(item.get("id") or os.path.splitext(storage_name)[0]), f"logo_{idx}")
            option_id = option_id_base
            counter = 2
            while option_id in seen:
                option_id = sanitize_id(f"{option_id_base}_{counter}", f"logo_{idx}_{counter}")
                counter += 1
            seen.add(option_id)
            sort_order = normalize_int(item.get("sort_order", item.get("order", idx)), idx, 1, 9999)
            result.append({
                "id": option_id,
                "name": normalize_string(item.get("name"), os.path.splitext(storage_name)[0]),
                "storage_name": storage_name,
                "sort_order": sort_order,
            })
    result.sort(key=lambda option: (normalize_int(option.get("sort_order"), 9999, 1, 9999), str(option.get("name") or "").lower(), str(option.get("id") or "")))
    return result


def normalize_multi_value_ids(values: object) -> List[str]:
    return normalize_qr_field_ids(values)


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


def selected_qr_field_ids_from_source(fields: List[Dict], source: object) -> List[str]:
    values: object = []
    if hasattr(source, "getlist"):
        values = source.getlist("qr_field_ids")
    elif isinstance(source, dict):
        values = source.get("qr_field_ids", source.get("qr_fields", []))
    selected = normalize_qr_field_ids(values)
    if not selected:
        selected = [field.get("id") for field in fields if normalize_bool(field.get("always_use_for_qr"), False)]
    valid_ids = {field.get("id") for field in fields if field_supports_text(field)}
    return [field_id for field_id in selected if field_id in valid_ids]


def qr_payload_from_field_forms(field_forms: List[Dict], selected_field_ids: List[str]) -> str:
    selected_lookup = set(normalize_qr_field_ids(selected_field_ids))
    if not selected_lookup:
        return ""
    parts: List[str] = []
    for field in field_forms:
        if field.get("id") not in selected_lookup:
            continue
        if not field_supports_text(field):
            continue
        try:
            value = normalize_qr_value(field.get("value", ""))
        except Exception:
            value = ""
        if value:
            parts.append(value)
    return " - ".join(parts)


def safe_qr_payload_from_field_forms(field_forms: List[Dict], selected_field_ids: List[str]) -> str:
    try:
        return qr_payload_from_field_forms(field_forms, selected_field_ids)
    except Exception as exc:
        LOGGER.warning("Failed to build QR payload from selected fields: %s", exc)
        return ""


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


def require_requested_profile(opts: Dict) -> Dict:
    profile = opts.get("requested_profile")
    if not isinstance(profile, dict) or not profile.get("id"):
        raise ValueError(ui_text(opts, "no_profiles_configured"))
    return profile


def ensure_field_manager_enabled(opts: Dict) -> None:
    if not normalize_bool(opts.get("show_global_field_config"), True):
        raise ValueError(ui_text(opts, "field_manager_disabled_message"))


def validate_field_forms(field_forms: List[Dict], language: str, strict_required: bool = True) -> List[Dict]:
    validated: List[Dict] = []
    for field in field_forms:
        value = str(field.get("value") or "").strip() if field_supports_text(field) else ""
        selected_logo_ids = normalize_multi_value_ids(field.get("selected_logo_ids", field.get("value", []))) if field_supports_logos(field) else []
        if field.get("number_only") and value and not value.isdigit():
            raise ValueError(ui_text(language, "field_numbers_only", field=field["name"]))
        if strict_required and field.get("required") and field.get("print_enabled") and not value and not selected_logo_ids:
            raise ValueError(ui_text(language, "field_required", field=field["name"]))
        validated.append({**field, "value": value, "selected_logo_ids": selected_logo_ids})
    return validated


def bind_field_forms_to_profile(field_forms: List[Dict], profile: Dict) -> List[Dict]:
    return [{**field, "profile": profile} for field in field_forms]


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
        is_footer = field.get("position") == "footer"
        target = footer if is_footer else body
        default_gap_mm = FOOTER_GAP_MM if is_footer else FIELD_GAP_MM
        source_field_id = field.get("id") or ""
        field_blocks: List[Dict] = []
        if field_supports_logos(field):
            selected_lookup = set(normalize_multi_value_ids(field.get("selected_logo_ids", [])))
            selected_options = [option for option in normalize_logo_options(field.get("logo_options", [])) if option.get("id") in selected_lookup]
            if selected_options:
                field_blocks.append({
                    "type": "logo_row",
                    "alignment": field["alignment"],
                    "logos": selected_options,
                    "logo_height_mm": field.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM),
                    "footer_bottom_margin_mm": field.get("footer_bottom_margin_mm", 0.0),
                    "profile": field.get("profile"),
                    "source_field_id": source_field_id,
                })
        text = apply_field_text_transform(field) if field_supports_text(field) else ""
        if text:
            field_blocks.append({
                "type": "text",
                "value": text,
                "alignment": field["alignment"],
                "font_family": field["font_family"],
                "font_size_mm": field["font_size_mm"],
                "bold": field["bold"],
                "italic": field["italic"],
                "underline": field["underline"],
                "max_lines": field["max_lines"],
                "footer_bottom_margin_mm": field.get("footer_bottom_margin_mm", 0.0),
                "profile": field.get("profile"),
                "source_field_id": source_field_id,
            })
        grouped_gap_mm = float(field.get("footer_logo_text_gap_mm", GROUPED_FIELD_GAP_MM) or 0.0) if is_footer else GROUPED_FIELD_GAP_MM
        for index, block in enumerate(field_blocks):
            block["gap_before_mm"] = grouped_gap_mm if index > 0 else (default_gap_mm if target else 0.0)
            target.append(block)
    return body, footer


def printer_dpi(profile: Dict | int | float | None = None) -> int:
    if isinstance(profile, dict):
        return normalize_int(profile.get("printer_dpi"), DEFAULT_PRINTER_DPI, 100, 1200)
    if profile is None:
        return DEFAULT_PRINTER_DPI
    return normalize_int(profile, DEFAULT_PRINTER_DPI, 100, 1200)


def dots_per_mm(profile: Dict | int | float | None = None) -> float:
    return printer_dpi(profile) / 25.4


def mm_to_dots(mm_value: float, profile: Dict | int | float | None = None) -> int:
    return max(1, int(round(float(mm_value) * dots_per_mm(profile))))


def mm_to_signed_dots(mm_value: float, profile: Dict | int | float | None = None) -> int:
    return int(round(float(mm_value) * dots_per_mm(profile)))


def dots_to_mm(dots: int, profile: Dict | int | float | None = None) -> float:
    return round(dots / dots_per_mm(profile), 1)


def printer_max_width_dots(profile: Dict | int | float | None = None) -> int:
    return mm_to_dots(PRINTER_MAX_WIDTH_MM, profile)


def profile_qr_enabled(profile: Dict, qr_value: object = None) -> bool:
    qr_size_mm = normalize_float(profile.get("qr_size_mm"), 170.0, 0.0, 300.0)
    if qr_size_mm <= 0:
        return False
    if qr_value is None:
        return True
    return bool(normalize_qr_value(qr_value))


def effective_layout(profile: Dict) -> Dict:
    requested_width_dots = mm_to_dots(profile["label_width_mm"], profile)
    requested_height_dots = mm_to_dots(profile["label_height_mm"], profile)
    qr_size_mm = normalize_float(profile.get("qr_size_mm"), 170.0, 0.0, 300.0)
    qr_size_dots = mm_to_dots(qr_size_mm, profile) if qr_size_mm > 0 else 0
    top_margin_dots = mm_to_dots(profile["top_margin_mm"], profile)
    text_box_margin_left_dots = mm_to_dots(normalize_float(profile.get("text_box_margin_left_mm"), DEFAULT_TEXTBOX_MARGIN_LEFT_MM, 0.0, 100.0), profile)
    text_box_margin_right_dots = mm_to_dots(normalize_float(profile.get("text_box_margin_right_mm"), DEFAULT_TEXTBOX_MARGIN_RIGHT_MM, 0.0, 100.0), profile)
    footer_bottom_margin_dots = mm_to_dots(profile.get("footer_bottom_margin_mm", 0.0), profile)
    max_width_dots = printer_max_width_dots(profile)
    effective_width_dots = min(requested_width_dots, max_width_dots)
    return {
        "requested_width_dots": requested_width_dots,
        "requested_height_dots": requested_height_dots,
        "qr_size_dots": qr_size_dots,
        "top_margin_dots": top_margin_dots,
        "text_box_margin_left_dots": text_box_margin_left_dots,
        "text_box_margin_right_dots": text_box_margin_right_dots,
        "footer_bottom_margin_dots": footer_bottom_margin_dots,
        "effective_width_dots": effective_width_dots,
        "width_warning": requested_width_dots > max_width_dots,
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


def image_to_graphic_bytes(img: Image.Image) -> Tuple[int, int, bytes]:
    if img.mode != "1":
        img = img.convert("1")
    width, height = img.size
    bytes_per_row = (width + 7) // 8
    total_bytes = bytes_per_row * height
    pixels = img.load()
    payload = bytearray()
    for y in range(height):
        for byte_idx in range(bytes_per_row):
            value = 0
            for bit in range(8):
                x = (byte_idx * 8) + bit
                value <<= 1
                if x < width and pixels[x, y] == 0:
                    value |= 1
            payload.append(value)
    return total_bytes, bytes_per_row, bytes(payload)


def image_to_gfa(img: Image.Image) -> Tuple[int, int, str]:
    total_bytes, bytes_per_row, payload = image_to_graphic_bytes(img)
    return total_bytes, bytes_per_row, payload.hex().upper()


def image_to_z64_gfa(img: Image.Image) -> Tuple[int, int, str]:
    total_bytes, bytes_per_row, payload = image_to_graphic_bytes(img)
    compressed = zlib.compress(payload, level=9)
    encoded = base64.b64encode(compressed).decode("ascii")
    crc = binascii.crc_hqx(encoded.encode("ascii"), 0)
    return total_bytes, bytes_per_row, f":Z64:{encoded}:{crc:04X}"


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
    start_size = max(10, mm_to_dots(block["font_size_mm"], block.get("profile")))
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


def load_logo_image(storage_name: object) -> Image.Image:
    safe_name = sanitize_storage_name(storage_name)
    if not safe_name:
        raise FileNotFoundError("Logo not found")
    path = logo_asset_path(safe_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Logo not found: {safe_name}")
    with Image.open(path) as img:
        return img.convert("RGBA").copy()


def fit_logo_image(block: Dict, max_width: int) -> Image.Image:
    logo = load_logo_image(block.get("storage_name"))
    target_h = max(1, mm_to_dots(block.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM), block.get("profile")))
    scale = target_h / max(1, logo.height)
    width = max(1, int(round(logo.width * scale)))
    height = max(1, int(round(logo.height * scale)))
    if width > max_width:
        scale = max_width / max(1, logo.width)
        width = max(1, int(round(logo.width * scale)))
        height = max(1, int(round(logo.height * scale)))
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def fit_logo_row_images(block: Dict, max_width: int) -> List[Image.Image]:
    logos = []
    for option in normalize_logo_options(block.get("logos", [])):
        storage_name = option.get("storage_name")
        if not storage_name:
            continue
        logo_block = {"storage_name": storage_name, "logo_height_mm": block.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM), "profile": block.get("profile")}
        logos.append(fit_logo_image(logo_block, max_width))
    if not logos:
        return []
    gap = mm_to_dots(DEFAULT_LOGO_GAP_MM, block.get("profile"))
    total_width = sum(logo.width for logo in logos) + (gap * max(0, len(logos) - 1))
    if total_width <= max_width:
        return logos
    scale = max_width / max(1, total_width)
    resized = []
    for logo in logos:
        width = max(1, int(round(logo.width * scale)))
        height = max(1, int(round(logo.height * scale)))
        resized.append(logo.resize((width, height), Image.Resampling.LANCZOS))
    return resized


def draw_aligned_logo(img: Image.Image, logo: Image.Image, y: int, box_left: int, box_width: int, alignment: str) -> int:
    logo_w, logo_h = logo.size
    if alignment == "left":
        x = box_left
    elif alignment == "right":
        x = box_left + box_width - logo_w
    else:
        x = box_left + (box_width - logo_w) / 2
    x = int(round(x))
    y = int(round(y))
    img.alpha_composite(logo, (x, y))
    return y + logo_h


def draw_aligned_logo_row(img: Image.Image, logos: List[Image.Image], y: int, box_left: int, box_width: int, alignment: str, profile: Dict) -> int:
    if not logos:
        return y
    gap = mm_to_dots(DEFAULT_LOGO_GAP_MM, profile)
    total_width = sum(logo.width for logo in logos) + (gap * max(0, len(logos) - 1))
    max_height = max(logo.height for logo in logos)
    if alignment == "left":
        x = box_left
    elif alignment == "right":
        x = box_left + box_width - total_width
    else:
        x = box_left + (box_width - total_width) / 2
    current_x = int(round(x))
    top_y = int(round(y))
    for idx, logo in enumerate(logos):
        logo_y = top_y + max_height - logo.height
        img.alpha_composite(logo, (current_x, int(round(logo_y))))
        current_x += logo.width
        if idx < len(logos) - 1:
            current_x += gap
    return top_y + max_height


def draw_body_blocks(img: Image.Image, draw: ImageDraw.ImageDraw, start_y: int, box_left: int, box_width: int, body_blocks: List[Dict], profile: Dict) -> int:
    current_y = start_y
    for block in body_blocks:
        gap_before_mm = float(block.get("gap_before_mm", FIELD_GAP_MM if current_y > start_y else 0.0) or 0.0)
        if gap_before_mm > 0:
            current_y += mm_to_dots(gap_before_mm, profile)
        if block.get("type") == "logo_row":
            logos = fit_logo_row_images(block, box_width)
            current_y = draw_aligned_logo_row(img, logos, current_y, box_left, box_width, block["alignment"], profile)
        elif block.get("type") == "logo":
            logo = fit_logo_image(block, box_width)
            current_y = draw_aligned_logo(img, logo, current_y, box_left, box_width, block["alignment"])
        else:
            font, lines, resolved = fit_block_lines(draw, block["value"], block, box_width)
            spacing = max(4, resolved // 7)
            current_y = draw_aligned_lines(draw, lines, current_y, box_left, box_width, font, block["alignment"], block["underline"], spacing)
    return current_y


def block_height(draw: ImageDraw.ImageDraw, block: Dict, box_width: int) -> Tuple[int, object, object, int]:
    if block.get("type") == "logo_row":
        logos = fit_logo_row_images(block, box_width)
        return (max((logo.height for logo in logos), default=0), logos, None, 0)
    if block.get("type") == "logo":
        logo = fit_logo_image(block, box_width)
        return logo.height, logo, None, 0
    font, lines, resolved = fit_block_lines(draw, block["value"], block, box_width)
    spacing = max(4, resolved // 7)
    line_h = text_line_height(draw, font)
    total = (line_h * len(lines)) + (max(0, len(lines) - 1) * spacing)
    return total, font, lines, spacing


def draw_footer_blocks(img: Image.Image, draw: ImageDraw.ImageDraw, bottom_y: int, box_left: int, box_width: int, footer_blocks: List[Dict], profile: Dict) -> int:
    current_bottom = bottom_y
    for block in reversed(footer_blocks):
        current_bottom -= mm_to_dots(block.get("footer_bottom_margin_mm", 0.0), profile)
        total_h, payload, lines, spacing = block_height(draw, block, box_width)
        top_y = current_bottom - total_h
        if block.get("type") == "logo_row":
            draw_aligned_logo_row(img, payload, top_y, box_left, box_width, block["alignment"], profile)
        elif block.get("type") == "logo":
            draw_aligned_logo(img, payload, top_y, box_left, box_width, block["alignment"])
        else:
            draw_aligned_lines(draw, lines, top_y, box_left, box_width, payload, block["alignment"], block["underline"], spacing)
        gap_before_mm = float(block.get("gap_before_mm", FOOTER_GAP_MM if top_y < bottom_y else 0.0) or 0.0)
        current_bottom = top_y - mm_to_dots(gap_before_mm, profile)
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


def draw_preview_outline(draw: ImageDraw.ImageDraw, left: int, top: int, width: int, bottom: int, profile: Dict) -> None:
    if width <= 0 or bottom <= top:
        return
    border_width = max(2, int(round(dots_per_mm(profile) * 0.5)))
    draw.rectangle((left, top, left + width - 1, bottom - 1), outline=(220, 38, 38), width=border_width)


def text_box_bottom_for_preview(text_top: int, body_bottom: int, footer_bottom: int | None, canvas_h: int, profile: Dict) -> int:
    gap = mm_to_dots(FIELD_GAP_MM, profile)
    if footer_bottom is not None:
        return max(text_top + 1, footer_bottom)
    content_bottom = body_bottom - gap if body_bottom > text_top else body_bottom
    return max(text_top + 1, min(canvas_h, content_bottom))


def render_portrait_content(printable_w: int, canvas_h: int, qr_value: str, body_blocks: List[Dict], footer_blocks: List[Dict], profile: Dict, preview: bool) -> Image.Image:
    layout = effective_layout(profile)
    img = Image.new("RGBA", (printable_w, canvas_h), color=(255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    has_qr = profile_qr_enabled(profile, qr_value)
    text_right = layout["text_box_margin_right_dots"]
    text_left = layout["text_box_margin_left_dots"] if has_qr else text_right
    text_width = max(1, printable_w - text_left - text_right)
    current_y = layout["top_margin_dots"]
    if has_qr:
        qr_size = min(layout["qr_size_dots"], printable_w)
        qr_left = max((printable_w - qr_size) // 2, 0)
        qr_top = layout["top_margin_dots"]
        try:
            qr_img = build_qr_image(normalize_qr_value(qr_value), qr_size, profile).convert("RGB")
            img.paste(qr_img, (qr_left, qr_top))
            if preview:
                draw_preview_outline(draw, qr_left, qr_top, qr_size, qr_top + qr_size, profile)
            current_y = qr_top + qr_size + mm_to_dots(DEFAULT_QR_TEXT_GAP_MM, profile)
        except Exception as exc:
            LOGGER.warning("Failed to render portrait QR block, continuing without QR: %s", exc)
            has_qr = False
            text_left = text_right
            text_width = max(1, printable_w - text_left - text_right)
            current_y = layout["top_margin_dots"]
    text_top = current_y
    body_bottom = draw_body_blocks(img, draw, current_y, text_left, text_width, body_blocks, profile)
    footer_bottom = None
    if footer_blocks:
        footer_bottom = canvas_h - layout["footer_bottom_margin_dots"]
        draw_footer_blocks(img, draw, footer_bottom, text_left, text_width, footer_blocks, profile)
    if preview and (body_blocks or footer_blocks):
        draw_preview_outline(draw, text_left, text_top, text_width, text_box_bottom_for_preview(text_top, body_bottom, footer_bottom, canvas_h, profile), profile)
    return img


def render_rotated_content(printable_w: int, canvas_h: int, qr_value: str, body_blocks: List[Dict], footer_blocks: List[Dict], profile: Dict, preview: bool, rotation_degrees: int) -> Image.Image:
    layout = effective_layout(profile)
    logical_w = canvas_h
    logical_h = printable_w
    landscape = Image.new("RGBA", (logical_w, logical_h), color=(255, 255, 255, 255))
    draw = ImageDraw.Draw(landscape)
    has_qr = profile_qr_enabled(profile, qr_value)
    left_margin = layout["text_box_margin_left_dots"]
    right_margin = layout["text_box_margin_right_dots"]
    text_left = left_margin if has_qr else right_margin
    text_width = max(1, logical_w - text_left - right_margin)
    text_top = layout["top_margin_dots"]
    if has_qr:
        qr_size = min(layout["qr_size_dots"], logical_h)
        qr_left = min(max(layout["top_margin_dots"], 0), max(0, logical_w - qr_size))
        qr_top = max((logical_h - qr_size) // 2, 0)
        try:
            qr_img = build_qr_image(normalize_qr_value(qr_value), qr_size, profile).convert("RGB")
            landscape.paste(qr_img, (qr_left, qr_top))
            if preview:
                draw_preview_outline(draw, qr_left, qr_top, qr_size, qr_top + qr_size, profile)
            inter_block_gap = left_margin
            text_left = min(logical_w, qr_left + qr_size + inter_block_gap)
            text_width = max(1, logical_w - text_left - right_margin)
            text_top = mm_to_dots(DEFAULT_TEXTBOX_TOP_MARGIN_MM, profile)
        except Exception as exc:
            LOGGER.warning("Failed to render rotated QR block, continuing without QR: %s", exc)
            has_qr = False
            text_left = right_margin
            text_width = max(1, logical_w - text_left - right_margin)
            text_top = layout["top_margin_dots"]
    body_bottom = draw_body_blocks(landscape, draw, text_top, text_left, text_width, body_blocks, profile)
    footer_bottom = None
    if footer_blocks:
        footer_bottom = logical_h - layout["footer_bottom_margin_dots"]
        draw_footer_blocks(landscape, draw, footer_bottom, text_left, text_width, footer_blocks, profile)
    if preview and (body_blocks or footer_blocks):
        draw_preview_outline(draw, text_left, text_top, text_width, text_box_bottom_for_preview(text_top, body_bottom, footer_bottom, logical_h, profile), profile)
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
    canvas = Image.new("RGBA", (requested_w, requested_h), color=(255, 255, 255, 255))
    printable_left = max((requested_w - printable_w) // 2, 0)
    draw_background_for_preview(canvas, requested_w, requested_h, printable_left, printable_w)
    canvas.alpha_composite(printable_image, (printable_left, 0))
    return orient_preview_for_display(canvas, rotation_degrees)


def apply_print_shift(printable_image: Image.Image, profile: Dict) -> Image.Image:
    shift_x_dots = mm_to_signed_dots(profile.get("print_shift_x_mm", 0.0), profile) if profile.get("print_shift_x_mm") else 0
    shift_y_dots = mm_to_signed_dots(profile.get("print_shift_y_mm", 0.0), profile) if profile.get("print_shift_y_mm") else 0
    if shift_x_dots == 0 and shift_y_dots == 0:
        return printable_image
    shifted = Image.new("RGBA", printable_image.size, color=(255, 255, 255, 255))
    shifted.alpha_composite(printable_image, (shift_x_dots, shift_y_dots))
    return shifted


def build_zpl(qr_value: str, field_forms: List[Dict], copies: int, profile: Dict) -> str:
    layout = effective_layout(profile)
    pw = layout["effective_width_dots"]
    ll = layout["requested_height_dots"]
    label_img = apply_print_shift(render_label_image(qr_value, field_forms, profile, preview=False), profile).convert("1")
    try:
        total_bytes, bytes_per_row, graphic_data = image_to_z64_gfa(label_img)
    except Exception:
        LOGGER.exception("Falling back to ASCII hex graphic encoding")
        total_bytes, bytes_per_row, graphic_data = image_to_gfa(label_img)
    return f"""^XA
^CI28
^PW{pw}
^LL{ll}
^LH0,0
^FO0,0^GFA,{total_bytes},{total_bytes},{bytes_per_row},{graphic_data}^FS
^PQ{copies},0,1,N
^XZ"""


def send_to_printer(host: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    LOGGER.info("Sending %s bytes to printer %s:%s", len(data), host, port)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        sock.sendall(data)
        try:
            sock.shutdown(socket.SHUT_WR)
        except Exception:
            pass
    LOGGER.info("Finished sending label payload to printer %s:%s", host, port)


def preview_query_from_form(form: Dict[str, object], field_forms: List[Dict], profile_id: str | None = None) -> str:
    params: List[Tuple[str, str]] = [("copies", str(form.get("copies", "1")))]
    if profile_id:
        params.append(("profile_id", str(profile_id)))
    for field_id in normalize_qr_field_ids(form.get("qr_field_ids", [])):
        params.append(("qr_field_ids", field_id))
    for field in field_forms:
        value_key = field_value_name(field["id"])
        logo_value_key = field_logo_value_name(field["id"])
        logo_present_key = field_logo_present_name(field["id"])
        if field.get("supports_text"):
            params.append((value_key, str(field.get("value", ""))))
        if field.get("supports_logos"):
            params.append((logo_present_key, "1"))
            for logo_id in normalize_multi_value_ids(field.get("selected_logo_ids", [])):
                params.append((logo_value_key, logo_id))
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
        "sort_order": 1,
        "footer_text": False,
        "footer_bottom_margin_mm": 0.0,
        "footer_logo_text_gap_mm": GROUPED_FIELD_GAP_MM,
        "append_current_date": False,
        "always_use_for_qr": False,
        "value_options": [],
        "value_options_text": "",
        "default_logo_ids": [],
        "logo_field": False,
        "logo_height_mm": DEFAULT_LOGO_HEIGHT_MM,
        "logo_options": [],
        "max_lines": 3,
    }


def editor_form_from_field(field: Dict | None) -> Dict:
    if not field:
        return blank_editor_form()
    supports_logos = field_supports_logos(field)
    default_logo_ids = normalize_multi_value_ids(field.get("default_logo_ids", [])) if supports_logos else []
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
        "sort_order": normalize_int(field.get("sort_order"), 1, 1, 9999),
        "footer_text": normalize_bool(field.get("footer_text"), field.get("position") == "footer"),
        "footer_bottom_margin_mm": field.get("footer_bottom_margin_mm", 0.0),
        "append_current_date": field.get("append_current_date", False),
        "always_use_for_qr": field.get("always_use_for_qr", False),
        "value_options": normalize_value_options(field.get("value_options", [])),
        "value_options_text": value_options_text(field.get("value_options", [])),
        "default_logo_ids": default_logo_ids,
        "logo_field": supports_logos,
        "logo_height_mm": field.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM),
        "logo_options": [{**option, "asset_url": logo_asset_url(option.get("storage_name")), "selected_default": option.get("id") in set(default_logo_ids)} for option in normalize_logo_options(field.get("logo_options", []))],
        "supports_logos": supports_logos,
        "max_lines": field.get("max_lines", 3),
    }


def field_store_map(fields: List[Dict]) -> Dict[str, Dict]:
    return {field["id"]: editor_form_from_field(field) for field in fields}


def validate_and_normalize_editor_payload(source: Dict, language: str) -> Tuple[str, Dict]:
    raw_name = normalize_string(source.get("name"), "")
    if not raw_name:
        raise ValueError(ui_text(language, "field_name_required"))
    original_field_id = sanitize_id(str(source.get("original_field_id") or ""), "")
    logo_field = normalize_bool(source.get("logo_field"), False) or bool(normalize_logo_options(source.get("logo_options", []))) or bool(normalize_multi_value_ids(source.get("default_logo_ids", [])))
    normalized = normalize_profile_field(
        {
            "id": source.get("id") or raw_name,
            "name": raw_name,
            "default_value": source.get("default_value", ""),
            "default_logo_ids": source.get("default_logo_ids", []),
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
            "sort_order": source.get("sort_order", 1),
            "footer_text": source.get("footer_text"),
            "footer_bottom_margin_mm": source.get("footer_bottom_margin_mm", 0.0),
            "footer_logo_text_gap_mm": source.get("footer_logo_text_gap_mm", GROUPED_FIELD_GAP_MM),
            "append_current_date": source.get("append_current_date"),
            "always_use_for_qr": source.get("always_use_for_qr"),
            "value_options": normalize_value_options(source.get("value_options_text", source.get("value_options", []))),
            "logo_field": source.get("logo_field"),
            "logo_height_mm": source.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM),
            "logo_options": source.get("logo_options", []),
            "max_lines": source.get("max_lines", 3),
        },
        1,
    )
    return original_field_id, normalized


def remove_logo_assets(logo_options: List[Dict[str, str]]) -> None:
    for option in normalize_logo_options(logo_options):
        path = logo_asset_path(option.get("storage_name"))
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as exc:
                LOGGER.warning("Failed to remove logo asset %s: %s", path, exc)


def save_uploaded_logo_options(files: List[object], language: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    ensure_asset_directories()
    uploaded: List[Dict[str, str]] = []
    created: List[Dict[str, str]] = []
    for idx, file_storage in enumerate(files, start=1):
        if not file_storage or not getattr(file_storage, 'filename', ''):
            continue
        filename = os.path.basename(str(file_storage.filename))
        if not filename.lower().endswith('.png'):
            raise ValueError(ui_text(language, 'logo_png_only'))
        try:
            file_storage.stream.seek(0)
            with Image.open(file_storage.stream) as img:
                if (img.format or '').upper() != 'PNG':
                    raise ValueError(ui_text(language, 'logo_png_only'))
                rendered = img.convert('RGBA')
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(ui_text(language, 'logo_upload_invalid', error=exc)) from exc
        storage_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}.png"
        rendered.save(logo_asset_path(storage_name), format='PNG', optimize=True)
        option = {
            'id': sanitize_id(os.path.splitext(filename)[0], f'logo_{idx}'),
            'name': os.path.splitext(filename)[0] or f'Logo {idx}',
            'storage_name': storage_name,
            'sort_order': idx,
        }
        uploaded.append(option)
        created.append(option)
    return normalize_logo_options(uploaded), created


def resolve_logo_options_for_save(profile: Dict, source: object, files: object, language: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    original_field_id = sanitize_id(str(getattr(source, 'get', lambda *_: '')('original_field_id') or ''), '')
    existing_field = next((field for field in profile.get('fields', []) if field.get('id') == original_field_id), None)
    existing_logo_options = normalize_logo_options(existing_field.get('logo_options', [])) if existing_field else []
    remove_ids = set(normalize_multi_value_ids(getattr(source, 'getlist', lambda *_: [])('remove_logo_ids')))
    kept = [dict(option) for option in existing_logo_options if option.get('id') not in remove_ids]
    uploaded, created = save_uploaded_logo_options(getattr(files, 'getlist', lambda *_: [])('logo_files'), language)
    max_existing_order = max((normalize_int(option.get('sort_order'), 0, 0, 9999) for option in kept), default=0)
    normalized_uploaded = []
    for idx, option in enumerate(uploaded, start=1):
        normalized_uploaded.append({**option, 'sort_order': max_existing_order + idx})
    merged = kept + normalized_uploaded
    for idx, option in enumerate(merged, start=1):
        option_id = option.get('id')
        requested_order = getattr(source, 'get', lambda *_: None)(f'logo_order_{option_id}') if option_id else None
        option['sort_order'] = normalize_int(requested_order, normalize_int(option.get('sort_order'), idx, 1, 9999), 1, 9999)
    merged = normalize_logo_options(merged)
    created_ids = {option.get('id') for option in normalized_uploaded}
    created_with_order = [option for option in merged if option.get('id') in created_ids]
    return merged, created_with_order


def save_global_field(original_field_id: str, field: Dict, language: str) -> None:
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    fields = list(load_field_store(profiles))
    new_id = field["id"]
    collision = next((existing for existing in fields if existing["id"] == new_id and existing["id"] != original_field_id), None)
    if collision:
        raise ValueError(ui_text(language, "field_duplicate_error", field_id=new_id))

    previous_field = next((existing for existing in fields if existing["id"] == original_field_id and original_field_id), None)
    updated = False
    for idx, existing in enumerate(fields):
        if existing["id"] == original_field_id and original_field_id:
            fields[idx] = field
            updated = True
            break
    if not updated:
        fields.append(field)
    save_field_store(fields)
    if previous_field:
        new_ids = {option.get('storage_name') for option in normalize_logo_options(field.get('logo_options', []))}
        remove_logo_assets([option for option in normalize_logo_options(previous_field.get('logo_options', [])) if option.get('storage_name') not in new_ids])
    LOGGER.info("Saved global field %s", field["id"])


def delete_global_field(field_id: str) -> bool:
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    fields = list(load_field_store(profiles))
    removed = [field for field in fields if field.get("id") == field_id]
    remaining = [field for field in fields if field.get("id") != field_id]
    changed = len(remaining) != len(fields)
    save_field_store(remaining)
    for field in removed:
        remove_logo_assets(field.get('logo_options', []))
    if changed:
        LOGGER.info("Deleted global field %s", field_id)
    return changed


def move_global_field(field_id: str, direction: str) -> Dict | None:
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    fields = list(load_field_store(profiles))
    ordered = sort_fields_by_order(fields)
    selected = next((field for field in ordered if field.get("id") == field_id), None)
    if selected is None:
        return None
    position = normalize_position(selected.get("position"), "body")
    group = [field for field in ordered if normalize_position(field.get("position"), "body") == position]
    ids = [field.get("id") for field in group]
    try:
        current_index = ids.index(field_id)
    except ValueError:
        return None
    if direction == "up":
        target_index = current_index - 1
    elif direction == "down":
        target_index = current_index + 1
    else:
        raise ValueError(f"Unsupported move direction: {direction}")
    if target_index < 0 or target_index >= len(group):
        return selected
    group[current_index], group[target_index] = group[target_index], group[current_index]
    new_sort_orders = {field.get("id"): idx for idx, field in enumerate(group, start=1)}
    rewritten = []
    for index, field in enumerate(ordered, start=1):
        updated = dict(field)
        if normalize_position(updated.get("position"), "body") == position:
            updated["sort_order"] = new_sort_orders.get(updated.get("id"), field_sort_order_key(updated, index))
        rewritten.append(updated)
    save_field_store(rewritten)
    LOGGER.info("Moved global field %s %s", field_id, direction)
    return next((field for field in rewritten if field.get("id") == field_id), selected)


def update_global_field_setting(field_id: str, setting: str, value: bool) -> Dict:
    if setting not in ALLOWED_QUICK_FIELD_SETTINGS:
        raise ValueError(f"Unsupported field setting: {setting}")
    opts, _, _ = load_options()
    profiles = parse_label_profiles(opts.get("label_profiles"))
    fields = list(load_field_store(profiles))
    for idx, field in enumerate(fields):
        if field.get("id") != field_id:
            continue
        updated = normalize_profile_field({**field, setting: value}, idx + 1)
        fields[idx] = updated
        save_field_store(fields)
        LOGGER.info("Updated global field setting %s=%s for %s", setting, value, field_id)
        return updated
    raise ValueError(f"Unknown field: {field_id}")


def wants_json_response() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    requested_with = (request.headers.get("X-Requested-With") or "").lower()
    return "application/json" in accept or requested_with == "fetch"


def render_page(form: Dict[str, object], opts: Dict, field_forms: List[Dict], result: Dict | None = None, field_result: Dict | None = None, editor_form: Dict | None = None) -> str:
    ui = get_ui_strings(opts.get("ui_language"))
    ui["intro_text"] = ui["intro_text"] if normalize_bool(opts.get("show_global_field_config"), True) else ui.get("intro_text_fields_hidden", ui["intro_text"])
    qr_selected_ids = normalize_qr_field_ids(form.get("qr_field_ids", []))
    qr_preview = safe_qr_payload_from_field_forms(field_forms, qr_selected_ids) or ui["none"]
    next_field_sort_order = max((field_sort_order_key(field, idx + 1) for idx, field in enumerate(opts.get("fields", []))), default=0) + 1
    editor_form = editor_form or {**blank_editor_form(), "sort_order": next_field_sort_order}
    preview_profiles = []
    for profile in opts.get("preview_profiles", []):
        preview_profiles.append({
            **profile,
            "printer_target": format_printer_target(profile, opts),
            "preview_query": preview_query_from_form(form, field_forms, profile.get("id")),
        })
    preview_profile_names_text = ", ".join(profile.get("name", "") for profile in preview_profiles) or ui["none"]
    ordered_configured_fields = sort_fields_by_order(opts.get("fields", []))
    move_flags: Dict[str, Dict[str, bool]] = {}
    for position in ("body", "footer"):
        group = [field for field in ordered_configured_fields if normalize_position(field.get("position"), "body") == position]
        for index, field in enumerate(group):
            move_flags[field.get("id", "")] = {
                "can_move_up": index > 0,
                "can_move_down": index < (len(group) - 1),
            }
    configured_fields = [
        {
            **field,
            "supports_logos": field_supports_logos(field),
            "logo_options": [{**option, "asset_url": logo_asset_url(option.get("storage_name"))} for option in normalize_logo_options(field.get("logo_options", []))],
            **move_flags.get(field.get("id", ""), {"can_move_up": False, "can_move_down": False}),
        }
        for field in ordered_configured_fields
    ]
    return render_template_string(
        HTML,
        ui=ui,
        result=result,
        field_result=field_result,
        form=form,
        field_forms=field_forms,
        configured_fields=configured_fields,
        preview_profiles=preview_profiles,
        preview_profile_names_text=preview_profile_names_text,
        qr_preview=qr_preview,
        ingress_base=ingress_base_path(),
        editor_form=editor_form,
        field_editor_json=field_store_map(opts.get("fields", [])),
        qr_selected_ids=qr_selected_ids,
        alignments=sorted(ALIGNMENTS),
        font_families=sorted(FONT_FAMILIES),
        field_positions=["body", "footer"],
        has_profiles=bool(opts.get("label_profiles")),
        show_global_field_config=normalize_bool(opts.get("show_global_field_config"), True),
        next_field_sort_order=next_field_sort_order,
    )


def api_field_forms_from_payload(fields: List[Dict], payload: Dict) -> List[Dict]:
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
    for field in fields:
        current = dict(field)
        supports_logos = field_supports_logos(field)
        supports_text = field_supports_text(field)
        if supports_text:
            if field["id"] in values and not isinstance(values[field["id"]], (list, tuple, set, dict)):
                current["value"] = str(values[field["id"]])
            elif field["id"] in lookup:
                current["value"] = str(lookup[field["id"]].get("value") or "")
            else:
                current["value"] = str(field.get("default_value", ""))
        else:
            current["value"] = ""
        if supports_logos:
            logo_values = payload.get("logo_field_values") if isinstance(payload.get("logo_field_values"), dict) else {}
            if field["id"] in logo_values:
                current["selected_logo_ids"] = normalize_multi_value_ids(logo_values[field["id"]])
            elif field["id"] in lookup:
                current["selected_logo_ids"] = normalize_multi_value_ids(lookup[field["id"]].get("logos") or lookup[field["id"]].get("logo_ids") or lookup[field["id"]].get("values") or [])
            elif field["id"] in values and isinstance(values[field["id"]], (list, tuple, set)):
                current["selected_logo_ids"] = normalize_multi_value_ids(values[field["id"]])
            else:
                current["selected_logo_ids"] = normalize_multi_value_ids(field.get("default_logo_ids", []))
            selected_lookup = set(current["selected_logo_ids"])
            current["logo_options"] = [{**option, "selected": option.get("id") in selected_lookup} for option in normalize_logo_options(field.get("logo_options", []))]
        else:
            current["selected_logo_ids"] = []
        current["supports_logos"] = supports_logos
        current["supports_text"] = supports_text
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
    LOGGER.info("Opened UI")
    field_result = None
    if opts.get("migration_notice"):
        field_result = {"success": True, "message": ui_text(opts, opts["migration_notice"])}
    return render_page(form, opts, field_forms, result=None, field_result=field_result)


@APP.route("/print", methods=["POST"])
def print_label():
    opts = load_runtime_options(request.values.get("profile_id") or request.args.get("profile_id") or None)
    form, field_forms = form_data_from_request(opts)
    result = {"success": False, "message": ui_text(opts, "unknown_error")}
    try:
        profile = require_requested_profile(opts)
        field_forms = validate_field_forms(field_forms, opts["ui_language"])
        qr_value = safe_qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
        copies = max(1, min(50, int(form.get("copies", "1"))))
        zpl = build_zpl(qr_value, bind_field_forms_to_profile(field_forms, profile), copies, profile)
        host, port = resolve_printer_target(profile, opts)
        LOGGER.info("Print request received: profile=%s copies=%s qr_payload=%r", profile.get("id"), copies, qr_value)
        send_to_printer(host, port, zpl)
        result = {"success": True, "message": ui_text(opts, "sent_labels_message", copies=copies, host=host, port=port, qr_payload=qr_value or ui_text(opts, "none"))}
    except Exception as exc:
        LOGGER.exception("Print failed")
        result = {"success": False, "message": ui_text(opts, "print_failed_message", error=exc)}
    if wants_json_response():
        return jsonify(result), (200 if result.get("success") else 400)
    return render_page(form, opts, field_forms, result=result)


@APP.route("/fields/save", methods=["POST"])
def save_field():
    opts = load_runtime_options()
    ensure_field_manager_enabled(opts)
    form, field_forms = form_data_from_request(opts)
    if not opts.get("label_profiles"):
        result = {"success": False, "message": ui_text(opts, "no_profiles_configured")}
        return render_page(form, opts, field_forms, field_result=result, editor_form=blank_editor_form())
    editor_form = blank_editor_form()
    result = None
    original_field_id = sanitize_id(str(request.form.get("original_field_id") or ""), "")
    existing_field = next((field for field in opts.get("fields", []) if field.get("id") == original_field_id), None)
    merged_logo_options: List[Dict[str, str]] = normalize_logo_options(existing_field.get('logo_options', [])) if existing_field else []
    created_logo_options: List[Dict[str, str]] = []
    try:
        merged_logo_options, created_logo_options = resolve_logo_options_for_save({"fields": opts.get("fields", [])}, request.form, request.files, opts["ui_language"])
        payload = {**request.form.to_dict(flat=True), "default_logo_ids": request.form.getlist("default_logo_ids"), "logo_options": merged_logo_options}
        original_field_id, normalized_field = validate_and_normalize_editor_payload(payload, opts["ui_language"])
        save_global_field(original_field_id, normalized_field, opts["ui_language"])
        opts = load_runtime_options()
        form, field_forms = form_data_from_request(opts)
        result = {"success": True, "message": ui_text(opts, "field_saved_message", field=normalized_field["name"])}
        editor_form = blank_editor_form()
    except Exception as exc:
        remove_logo_assets(created_logo_options)
        if created_logo_options:
            removed_ids = {option.get("id") for option in created_logo_options}
            merged_logo_options = [option for option in merged_logo_options if option.get("id") not in removed_ids]
        LOGGER.exception("Field save failed")
        editor_form = editor_form_from_field({
            "id": request.form.get("id", ""),
            "name": request.form.get("name", ""),
            "default_value": request.form.get("default_value", ""),
            "default_logo_ids": request.form.getlist("default_logo_ids"),
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
            "sort_order": request.form.get("sort_order", 1),
            "footer_text": normalize_bool(request.form.get("footer_text"), False),
            "footer_bottom_margin_mm": request.form.get("footer_bottom_margin_mm", 0.0),
            "footer_logo_text_gap_mm": request.form.get("footer_logo_text_gap_mm", GROUPED_FIELD_GAP_MM),
            "append_current_date": normalize_bool(request.form.get("append_current_date"), False),
            "always_use_for_qr": normalize_bool(request.form.get("always_use_for_qr"), False),
            "value_options": normalize_value_options(request.form.get("value_options_text", "")),
            "logo_field": normalize_bool(request.form.get("logo_field"), False),
            "logo_height_mm": request.form.get("logo_height_mm", DEFAULT_LOGO_HEIGHT_MM),
            "logo_options": merged_logo_options,
            "max_lines": request.form.get("max_lines", 3),
        })
        editor_form["original_field_id"] = request.form.get("original_field_id", "")
        result = {"success": False, "message": ui_text(opts, "field_save_failed", error=exc)}
    return render_page(form, opts, field_forms, field_result=result, editor_form=editor_form)


@APP.route("/fields/move", methods=["POST"])
def move_field():
    opts = load_runtime_options()
    ensure_field_manager_enabled(opts)
    form, field_forms = form_data_from_request(opts)
    if not opts.get("label_profiles"):
        result = {"success": False, "message": ui_text(opts, "no_profiles_configured")}
        return render_page(form, opts, field_forms, field_result=result)
    try:
        field_id = sanitize_id(str(request.form.get("field_id") or ""), "")
        direction = normalize_string(request.form.get("direction"), "")
        moved = move_global_field(field_id, direction)
        opts = load_runtime_options()
        form, field_forms = form_data_from_request(opts)
        result = {"success": moved is not None, "message": ui_text(opts, "field_saved_message", field=(moved or {}).get("name", field_id)) if moved else ui_text(opts, "field_save_failed", error=field_id or ui_text(opts, "unknown_error"))}
    except Exception as exc:
        LOGGER.exception("Field move failed")
        result = {"success": False, "message": ui_text(opts, "field_save_failed", error=exc)}
    return render_page(form, opts, field_forms, field_result=result)


@APP.route("/fields/delete", methods=["POST"])
def delete_field():
    opts = load_runtime_options()
    ensure_field_manager_enabled(opts)
    form, field_forms = form_data_from_request(opts)
    if not opts.get("label_profiles"):
        result = {"success": False, "message": ui_text(opts, "no_profiles_configured")}
        return render_page(form, opts, field_forms, field_result=result)
    result = None
    try:
        field_id = sanitize_id(str(request.form.get("field_id") or ""), "")
        deleted = delete_global_field(field_id)
        opts = load_runtime_options()
        form, field_forms = form_data_from_request(opts)
        result = {
            "success": deleted,
            "message": ui_text(opts, "field_deleted_message", field=field_id) if deleted else ui_text(opts, "field_delete_failed", error=field_id or ui_text(opts, "unknown_error")),
        }
    except Exception as exc:
        LOGGER.exception("Field delete failed")
        result = {"success": False, "message": ui_text(opts, "field_delete_failed", error=exc)}
    return render_page(form, opts, field_forms, field_result=result)


@APP.route("/fields/quick-update", methods=["POST"])
def quick_update_field_setting():
    try:
        opts = load_runtime_options()
        ensure_field_manager_enabled(opts)
        if not opts.get("label_profiles"):
            raise ValueError(ui_text(opts, "no_profiles_configured"))
        field_id = sanitize_id(str(request.form.get("field_id") or ""), "")
        setting = normalize_string(request.form.get("setting"), "")
        value = normalize_bool(request.form.get("value"), False)
        updated = update_global_field_setting(field_id, setting, value)
        return jsonify({"ok": True, "field": updated})
    except Exception as exc:
        LOGGER.exception("Quick field update failed")
        return jsonify({"ok": False, "error": str(exc)}), 400


@APP.route("/assets/logos/<path:storage_name>", methods=["GET"])
def serve_logo_asset(storage_name: str):
    safe_name = sanitize_storage_name(storage_name)
    if not safe_name:
        return Response("Not found", status=404)
    path = logo_asset_path(safe_name)
    if not os.path.exists(path):
        return Response("Not found", status=404)
    return send_file(path, mimetype="image/png")


@APP.route("/preview", methods=["GET"])
def preview():
    opts = load_runtime_options(request.values.get("profile_id") or request.args.get("profile_id") or None)
    form, field_forms = form_data_from_request(opts)
    try:
        profile = require_requested_profile(opts)
        field_forms = validate_field_forms(field_forms, opts["ui_language"], strict_required=False)
        qr_value = safe_qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
        copies = max(1, min(50, int(form.get("copies", "1"))))
        zpl = build_zpl(qr_value, bind_field_forms_to_profile(field_forms, profile), copies, profile)
        LOGGER.info("Generated ZPL preview for profile=%s copies=%s", profile.get("id"), copies)
        return Response(zpl, mimetype="text/plain; charset=utf-8")
    except Exception as exc:
        LOGGER.exception("ZPL preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/preview.png", methods=["GET"])
def preview_png():
    opts = load_runtime_options(request.values.get("profile_id") or request.args.get("profile_id") or None)
    form, field_forms = form_data_from_request(opts)
    try:
        profile = require_requested_profile(opts)
        field_forms = validate_field_forms(field_forms, opts["ui_language"], strict_required=False)
        qr_value = safe_qr_payload_from_field_forms(field_forms, normalize_qr_field_ids(form.get("qr_field_ids", [])))
        LOGGER.info("Generating PNG preview for profile=%s qr_value=%r", opts.get("requested_profile_id"), qr_value)
        img = render_label_image(qr_value, bind_field_forms_to_profile(field_forms, profile), profile, preview=True)
        bio = BytesIO()
        current_dpi = printer_dpi(profile)
        img.save(bio, format="PNG", dpi=(current_dpi, current_dpi), optimize=True)
        bio.seek(0)
        return send_file(bio, mimetype="image/png", download_name="label-preview.png")
    except Exception as exc:
        LOGGER.exception("PNG preview failed")
        return Response(ui_text(opts, "preview_failed_message", error=exc), status=400, mimetype="text/plain; charset=utf-8")


@APP.route("/api/print", methods=["POST"])
def api_print():
    payload = request.get_json(force=True, silent=False) or {}
    opts = load_runtime_options(str(payload.get("profile_id") or "") or None)
    try:
        profile = require_requested_profile(opts)
        field_forms = validate_field_forms(api_field_forms_from_payload(opts.get("fields", []), payload), opts["ui_language"])
        qr_field_ids = selected_qr_field_ids_from_source(opts.get("fields", []), payload)
        qr_value = safe_qr_payload_from_field_forms(field_forms, qr_field_ids) if qr_field_ids else normalize_qr_value(payload.get("qr_value", profile.get("qr_default_value", "")))
        copies = max(1, min(50, int(payload.get("copies", 1))))
        zpl = build_zpl(qr_value, bind_field_forms_to_profile(field_forms, profile), copies, profile)
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
