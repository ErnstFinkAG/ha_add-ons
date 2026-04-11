# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

This version adds per-field text block ordering so fields can switch render positions within the body or footer.

## What changed in v0.1.59

Footer fields can now render selected logos and text together, with the text placed below the logos. Mixed footer logo/text fields keep separate default text and default selected logos.

This version removes built-in default profiles and fields, keeps fields global, and lets the preview image print directly.

- there are no built-in default label profiles anymore
- there are no built-in default fields anymore
- create the first label profile in the add-on configuration before using the UI
- global fields still apply to all label profiles
- the preview area renders one preview card per profile with `show_in_preview: true`
- clicking a preview image prints that exact profile using the selected copy count
- setting `qr_size_mm: 0` disables QR generation completely for that label profile in preview and print
- each preview card still has its own print, ZPL preview, and PNG preview actions
- older per-profile field storage is merged into one global field list automatically

## Add-on config

Configure one or more label profiles in the add-on **Configuration** tab. On a fresh install, add your first profile there before you start using the web UI. Set `qr_size_mm: 0` on a profile when that label should not generate a QR code at all.

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
    footer_bottom_margin_mm: 0
    print_rotation_degrees: 90
    qr_quiet_zone_modules: 4
    qr_error_correction: M
    show_in_preview: false
```

`show_in_preview` defaults to `false` when omitted.

The add-on now starts with an empty `label_profiles` list by default, so nothing is pre-created for you.

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
- footer fields may combine logo selection and text in the same field
- `logo_height_mm`
- `max_lines`
- `sort_order`: render order inside the body or footer text block

## Web UI

In the add-on web UI you can:

- enter the shared field values once
- build the QR content by selecting one or more global fields
- see multiple preview cards at the same time
- print to the printer configured inside each visible profile
- preview PNG and ZPL per visible profile
- manage the global fields in one shared field section
- click a preview image to print that profile immediately

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
  "logo_field_values": {
    "footer": ["fink_logo", "iso_logo"]
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
