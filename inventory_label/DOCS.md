# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## What changed in v0.1.56

This version refines profile margins for print and adds new left-side margin controls.

- profile margin settings now affect print output only and are ignored in preview
- top and left profile margins now follow label orientation, so top stays top and left stays left on rotated labels
- each label profile now supports `left_margin_mm`
- each label profile now supports `text_block_margin_left_mm`
- the text block can be shifted to the right independently of the QR block
- the global-field and multi-profile preview behavior from v0.1.55 remains in place

## Add-on config

Configure one or more label profiles in the add-on **Configuration** tab.

Example:

```yaml
ui_language: de
label_profiles:
  - id: standard
    name: Standard
    printer_host: ""
    printer_dpi: 203
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 170
    top_margin_mm: 0
    left_margin_mm: 0
    text_block_margin_left_mm: 0
    footer_bottom_margin_mm: 0
    print_rotation_degrees: 0
    qr_quiet_zone_modules: 3
    qr_error_correction: M
    show_in_preview: true

  - id: rotated
    name: Rotated
    printer_host: ""
    printer_dpi: 203
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 160
    top_margin_mm: 0
    left_margin_mm: 0
    text_block_margin_left_mm: 0
    footer_bottom_margin_mm: 0
    print_rotation_degrees: 90
    qr_quiet_zone_modules: 4
    qr_error_correction: M
    show_in_preview: false
```

`show_in_preview` defaults to `false` when omitted.

Profile margin settings (`top_margin_mm`, `left_margin_mm`, `text_block_margin_left_mm`, `footer_bottom_margin_mm`) affect **print only**. The PNG preview ignores those profile margins so the preview stays layout-focused. On rotated labels, top and left margins follow the label orientation.

## Field management in the web UI

The web UI now manages **one global field set**.

Those fields:

- are defined once
- apply to every label profile
- are used by all preview cards and print actions

Field settings supported in the UI:

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
- `footer_text`
- `footer_bottom_margin_mm`
- `append_current_date`
- `always_use_for_qr`
- `value_options`
- `logo_field`
- `logo_height_mm`
- `max_lines`

## Web UI

In the add-on web UI you can:

- enter the shared field values once
- build the QR content by selecting one or more global fields
- see multiple preview cards at the same time
- print to the printer configured inside each visible profile
- preview PNG and ZPL per visible profile
- manage the global fields in one shared field section

## API

`POST /api/print`

Example payload:

```json
{
  "profile_id": "standard",
  "qr_field_ids": ["project_no", "project_name"],
  "copies": 1,
  "field_values": {
    "project_no": "250001",
    "project_name": "EFH Huggentobbler Biel",
    "element": "DE1",
    "weight": "1",
    "footer": "Ernst Fink AG, Schorenweg 144, 4585 Biezwil",
    "brand_logos": ["fink_logo", "iso_logo"]
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
