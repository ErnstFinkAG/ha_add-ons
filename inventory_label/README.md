# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## What changed in v0.1.44

This version splits the configuration into two layers:

- **Add-on Configuration tab**: create and edit label profiles
- **Add-on web UI**: create and edit fields for the selected label profile

This matches the Home Assistant add-on settings UI much better:

- `ui_language` stays global
- every label profile gets its own structured entry in the settings UI
- field definitions are stored separately per profile and are managed in the add-on UI
- legacy `label_profiles_yaml` is migrated automatically on first start

## Add-on config

Configure one or more label profiles in the add-on **Configuration** tab.

Example:

```yaml
ui_language: de
label_profiles:
  - id: standard
    name: Standard
    printer_host: ""
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 170
    top_margin_mm: 0
    footer_bottom_margin_mm: 0
    print_rotation_degrees: 0
    qr_quiet_zone_modules: 3
    qr_error_correction: M

  - id: rotated
    name: Rotated
    printer_host: ""
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 160
    top_margin_mm: 0
    footer_bottom_margin_mm: 0
    print_rotation_degrees: 90
    qr_quiet_zone_modules: 4
    qr_error_correction: M
```

## Field management in the web UI

For the currently selected label profile, the web UI now provides a dedicated field manager.

There you can:

- add a new field
- edit an existing field
- delete a field
- keep field definitions separate for each label profile

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
- `footer_text` (bottom-anchored footer text)
- `footer_bottom_margin_mm` (additional bottom margin for bottom-anchored footer text)
- `append_current_date`
- `always_use_for_qr`
- `value_options` (suggested values, free text still allowed)
- `max_lines`

## Web UI

Printer host and printer port are optional. The add-on can start without them, previews still work, and printing only becomes available once both are set. If no QR field is selected, or all selected values are empty, no QR code is rendered. New label profiles use 0 mm for all margin options. Field templates can now mark fields as always used for QR, field inputs can offer suggested values while still accepting free text, and fields can be marked as footer text so their values stay anchored at the bottom of the label. Footer fields can also have their own additional bottom margin in mm.

In the add-on web UI you can:

- choose the active label profile
- build the QR content by selecting one or more defined fields
- persist QR and print checkbox changes back into the label template when clicked
- enter each configured field value with optional suggestion lists and free text
- turn each field on/off for the current label
- preview PNG and ZPL
- print to the printer configured inside the selected profile
- manage fields for the selected profile in a separate field section

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
