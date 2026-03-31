# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## What changed in v0.1.39

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
- manage fields for the selected profile in a separate field section

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
