# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## What changed in v0.1.37

This version moves the full label definition into `label_profiles_yaml`.

Only `ui_language` remains global. Everything else is profile-specific, including:
- printer host
- printer port
- label size
- QR settings
- rotation
- all field definitions

Each profile contains a nested `fields:` list. Every field has its own label, default value, style, and print behavior.

## Add-on config

Configure one or more label profiles in the add-on **Configuration** tab with `label_profiles_yaml`.

Example:

```yaml
ui_language: de
label_profiles_yaml: |
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

  - id: rotated
    name: Rotated
    printer_host: 10.50.20.12
    printer_port: 9100
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 160
    top_margin_mm: 0
    footer_bottom_margin_mm: 4
    print_rotation_degrees: 90
    qr_default_value: ""
    qr_quiet_zone_modules: 4
    qr_error_correction: M
    fields:
      - id: article
        name: Artikel
        default_value: "123456"
        alignment: center
        font_family: sans
        font_size_mm: 16
        bold: true
        italic: false
        underline: false
        print_by_default: true
        required: true
        number_only: true
        position: body
      - id: description
        name: Beschreibung
        default_value: Lagerplatz Nord
        alignment: center
        font_family: sans
        font_size_mm: 11
        bold: false
        italic: false
        underline: false
        print_by_default: true
        position: body
```

## Field options

Every field inside `fields:` can use these settings:
- `id`
- `name`
- `default_value`
- `alignment`: `left`, `center`, `right`
- `font_family`: `sans`, `serif`, `mono`
- `font_size_mm`
- `bold`
- `italic`
- `underline`
- `print_by_default`
- `required`
- `number_only`
- `suffix`
- `position`: `body` or `footer`
- `append_current_date`
- `max_lines`

## Web UI

In the add-on web UI you can:
- choose the active label profile
- enter the QR value
- enter each configured field value
- turn each field on/off for the current label
- preview PNG and ZPL
- print to the printer configured inside the selected profile

## API

`POST /api/print`

Example payload:

```json
{
  "profile_id": "standard",
  "qr_value": "250001 - EFH Huggentobbler Biel",
  "copies": 1,
  "field_values": {
    "project_no": "250001",
    "project_name": "EFH Huggentobbler Biel",
    "element": "DE1",
    "weight": "1",
    "footer": "Ernst Fink AG, Schorenweg 144, 4585 Biezwil"
  },
  "print_fields": {
    "project_no": true,
    "project_name": true,
    "element": true,
    "weight": false,
    "footer": true
  }
}
```
