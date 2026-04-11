# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## What changed in v0.1.59

This version repairs the UI/config structure so it matches the requested behavior again.

- fields are now global and shared by all label profiles
- label profiles now support `show_in_preview`
- the web UI shows one preview card per profile with `show_in_preview: true`
- the old label profile switch was removed from the main form
- print-only profile margins remain separate from preview rendering
- `top_margin_mm`, `left_margin_mm`, and `text_block_margin_left_mm` stay orientation-aware for rotated labels

## Add-on config

Example:

```yaml
ui_language: de
label_profiles:
  - id: standard
    name: Standard
    printer_host: ""
    printer_port: 9100
    show_in_preview: true
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
```

## UI behavior

- field definitions are edited once and apply to every profile
- QR selection and field values are shared across the preview cards
- each preview card prints against its own configured printer/profile
- preview ignores profile print margins so the full usable area remains visible

## Notes

`show_in_preview` defaults to `false` when omitted in a profile.
