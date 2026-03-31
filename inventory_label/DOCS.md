# Inventory Label

Home Assistant add-on for printing large-format project or inventory labels to a networked Zebra ZT420 or ZT421 over raw ZPL on TCP port 9100.

This repository is intended to be hosted at:

`https://github.com/ErnstFinkAG/ha_add-ons`

## Current feature set

- 1 large QR code per label
- live PNG preview rendered from the same layout geometry used for printing
- preview is shown at the configured label size in mm to approximate a 1:1 on-screen view
- red preview-only border showing the full QR footprint including quiet zone
- multilingual UI with German and English via `ui_language`
- optional rotated print layout via `print_rotation_degrees` (`0`, `90`, or `270`)
- 3 configurable main text fields
- sign-off input with configured suggestions and free-text entry
- optional numeric-only weight field with per-print checkbox
- any number of custom text blocks can be added in the web UI with per-block value, print toggle, alignment, font family, font size, bold, italic, and underline
- optional footer anchored to the physical bottom of the label
- automatic current date appended to the footer
- per-field defaults, alignment, font family, font size, bold, italic, and underline
- default-value config fields may be left empty
- per-print checkboxes in the UI to hide field 2, field 3, weight, and the footer
- configurable QR payload template using `text1`, `text2`, and `text3`
- numeric-only first field input in the UI for project or inventory numbers
- configurable QR quiet zone and QR error-correction level
- ZPL preview and JSON print API

## Repository layout

```text
ha_add-ons/
├── repository.json
└── inventory_label/
    ├── app.py
    ├── config.yaml
    ├── Dockerfile
    ├── DOCS.md
    ├── README.md
    └── run.sh
```

## Printer target

This add-on is built around a Zebra ZT420 or ZT421 at 203 dpi.

Important width note:

A 203 dpi ZT420 or ZT421 has a maximum print width of 168 mm, or 1344 dots. When the configured label width is 170 mm, the add-on clamps the actual ZPL print width to the printer-safe maximum.

## Install in Home Assistant

1. Open **Settings → Add-ons → Add-on Store**.
2. Open the menu and choose **Repositories**.
3. Add `https://github.com/ErnstFinkAG/ha_add-ons`.
4. Install **Inventory Label**.
5. Configure the add-on.
6. Start the add-on.
7. Open the web UI.

## Current default configuration

```yaml
ui_language: de
printer_host: 10.50.20.12
printer_port: 9100
label_width_mm: 170
label_height_mm: 305
qr_size_mm: 170
top_margin_mm: 0
print_rotation_degrees: 0
field1_label: Projektnummer
field2_label: Projektname
field3_label: Element
field1_default_value: "250001"
field2_default_value: EFH Huggentobbler Biel
field3_default_value: DE1
field1_alignment: center
field2_alignment: center
field3_alignment: center
field1_font_family: sans
field2_font_family: sans
field3_font_family: sans
field1_font_size_mm: 18
field2_font_size_mm: 13
field3_font_size_mm: 18
field1_bold: true
field2_bold: false
field3_bold: false
field1_italic: false
field2_italic: false
field3_italic: false
field1_underline: false
field2_underline: false
field3_underline: false
sign_off_label: Sign-off
sign_off_default_value: ""
sign_off_options: ""
sign_off_alignment: center
sign_off_font_family: sans
sign_off_font_size_mm: 7
sign_off_bold: false
sign_off_italic: false
sign_off_underline: false
weight_label: Weight (kg)
weight_default_value: ""
weight_alignment: center
weight_font_family: sans
weight_font_size_mm: 7
weight_bold: false
weight_italic: false
weight_underline: false
footer_label: Footer
footer_default_value: Ernst Fink AG, Schorenweg 144, 4585 Biezwil
footer_alignment: center
footer_font_family: sans
footer_font_size_mm: 5
footer_bottom_margin_mm: 0
footer_bold: false
footer_italic: false
footer_underline: false
qr_value_template: "{text1 - text2}"
qr_quiet_zone_modules: 3
qr_error_correction: M
```

## Language setting

Use `ui_language` to switch the web UI between German and English.

Allowed values:

- `de`
- `en`

This setting changes the static UI text, status messages, and validation messages. Your configured field labels stay exactly as you define them.

## Rotated print layout

Use `print_rotation_degrees` to rotate the whole label layout while keeping the printer and media setup unchanged.

Allowed values:

- `0` = normal layout with the QR code above the text blocks
- `90` = rotated layout
- `270` = rotated layout in the opposite direction

In rotated mode, the QR code and text layout are turned together, so when you view the label in the rotated reading direction, the QR code sits to the left of the text blocks.

## Sign-off suggestions

Use `sign_off_options` to define suggestions shown in the UI. The operator can still type any custom name directly.

Supported separators in `sign_off_options`:

- one name per line
- commas
- semicolons

Example:

```yaml
sign_off_options: |
  Max Muster
  Erika Beispiel
  John Doe
```

## Weight field

The weight field is human-readable only and is not part of the QR payload.

Behavior:

- the UI only accepts digits
- the current label can enable or disable weight printing with a checkbox
- when weight is enabled, it prints on its own line as `<weight> kg`, even when field 3 is also enabled
- otherwise weight prints on its own line as `<weight> kg`

## QR payload template

`qr_value_template` is a free-text template.

Supported tokens:

- `text1`
- `text2`
- `text3`

Everything else is treated as literal text, including spaces, dashes, slashes, punctuation, and labels.

Examples:

- `text1`
- `text1 - text2`
- `text1 / text2 / text3`
- `Projekt text1 Element text3`

For compatibility, the add-on also accepts a single outer pair of braces, so this works too:

- `{text1 - text2}`

Fields not used in the QR template are still printed on the label in human-readable form if their print checkbox is enabled.

## QR settings

### `qr_quiet_zone_modules`

Controls the quiet zone around the QR code in QR modules.

### `qr_error_correction`

Allowed values:

- `L`
- `M`
- `Q`
- `H`

Higher levels give more redundancy and lower data capacity.


## Web UI custom text blocks

Use **Add custom block** in the add-on web UI to create any number of extra human-readable text blocks for the current label. Each custom block can have its own:

- UI label
- printed value
- print on/off toggle
- alignment
- font family
- font size
- bold
- italic
- underline

These custom blocks are previewed live and are included in ZPL preview, PNG preview, normal print, and the JSON API via `custom_blocks` or `custom_blocks_json`. The web UI also keeps the current custom blocks in browser local storage so they survive a normal page reload on the same device/browser.
