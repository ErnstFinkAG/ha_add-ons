
# Inventory Label

Home Assistant add-on for printing large Zebra labels to a network-connected ZT420/ZT421 using raw TCP on port 9100.

## Current model

Global add-on settings stay at the top level:
- `ui_language`
- `printer_host`
- `printer_port`

All label-specific settings now live inside `label_profiles` in the add-on Configuration tab.
The first profile acts as the default profile in the web UI.

## Main features

- Label profile selector in the add-on web UI
- Live PNG preview
- QR code with configurable quiet zone and error correction
- Optional 0 / 90 / 270 degree layout rotation
- Per-field font, size, bold, italic, underline, and alignment
- Optional sign-off, weight, footer, and custom text blocks
- Footer date appended automatically
- Duplicate current profile to clipboard as ready-to-paste YAML

## Configuration example

```yaml
ui_language: de
printer_host: 10.50.20.12
printer_port: 9100
label_profiles:
  - id: standard
    name: Standard
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 170
    top_margin_mm: 0
    print_rotation_degrees: 0
    field1_label: Projektnummer
    field2_label: Projektname
    field3_label: Element
    sign_off_label: Sign-off
    weight_label: Weight (kg)
    footer_label: Footer
    field1_default_value: "250001"
    field2_default_value: EFH Huggentobbler Biel
    field3_default_value: DE1
    sign_off_default_value: ""
    sign_off_options: ""
    weight_default_value: ""
    footer_default_value: Ernst Fink AG, Schorenweg 144, 4585 Biezwil
    field1_alignment: center
    field2_alignment: center
    field3_alignment: center
    sign_off_alignment: center
    weight_alignment: center
    footer_alignment: center
    field1_font_family: sans
    field2_font_family: sans
    field3_font_family: sans
    sign_off_font_family: sans
    weight_font_family: sans
    footer_font_family: sans
    field1_font_size_mm: 18
    field2_font_size_mm: 13
    field3_font_size_mm: 18
    sign_off_font_size_mm: 7
    weight_font_size_mm: 7
    footer_font_size_mm: 5
    footer_bottom_margin_mm: 0
    field1_bold: true
    field2_bold: false
    field3_bold: false
    sign_off_bold: false
    weight_bold: false
    footer_bold: false
    field1_italic: false
    field2_italic: false
    field3_italic: false
    sign_off_italic: false
    weight_italic: false
    footer_italic: false
    field1_underline: false
    field2_underline: false
    field3_underline: false
    sign_off_underline: false
    weight_underline: false
    footer_underline: false
    qr_value_template: "{text1 - text2}"
    qr_quiet_zone_modules: 3
    qr_error_correction: M
    default_print_text2: true
    default_print_text3: true
    default_print_weight: false
    default_print_footer: true
```

## Duplicate a profile

In the web UI, choose an existing profile and click **Duplicate profile to clipboard**.
The add-on copies a full ready-to-paste YAML block for a duplicated profile. Paste that block into `label_profiles` in the add-on Configuration tab, then adjust `id` and `name` if needed.

## Notes

- Field 1 accepts digits only.
- Weight accepts digits only.
- The selected profile controls preview, print, QR settings, rotation, and defaults.
- The add-on still contains legacy fallback support for older flat configs, but the intended setup is `label_profiles`.
