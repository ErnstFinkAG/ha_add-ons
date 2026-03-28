# Inventory Label

Home Assistant add-on for printing large-format project or inventory labels to a networked Zebra ZT420 or ZT421 over raw ZPL on TCP port 9100.

This repository is intended to be hosted at:

`https://github.com/ErnstFinkAG/ha_add-ons`

## Current feature set

- 1 large QR code per label
- live PNG preview rendered from the same layout geometry used for printing
- red preview-only border showing the full QR footprint including quiet zone
- 3 configurable main text fields
- sign-off input with configured suggestions and free-text entry
- optional numeric-only weight field with per-print checkbox
- optional footer anchored to the physical bottom of the label
- per-field defaults, alignment, font family, font size, bold, italic, and underline
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

Configured defaults in this version:

- printer host: `10.50.20.12`
- printer port: `9100`
- label size: `170 × 305 mm`
- QR size: `170 × 170 mm`
- top margin: `0 mm`

Important width note:

A 203 dpi ZT420 or ZT421 has a maximum print width of 168 mm, or 1344 dots. When the configured label width is 170 mm, the add-on clamps the actual ZPL print width to the printer-safe maximum.

## Install in Home Assistant

1. Open **Settings → Add-ons → Add-on Store**.
2. Open the menu and choose **Repositories**.
3. Add:

   ```text
   https://github.com/ErnstFinkAG/ha_add-ons
   ```

4. Install **Inventory Label**.
5. Configure the add-on.
6. Start the add-on.
7. Open the web UI.

## Current default configuration

```yaml
printer_host: 10.50.20.12
printer_port: 9100
label_width_mm: 170
label_height_mm: 305
qr_size_mm: 170
top_margin_mm: 0
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

## Sign-off suggestions

Use `sign_off_options` to define suggestions shown in the UI.

The operator can still type any custom name directly.

Supported separators in `sign_off_options`:

- one name per line
- commas
- semicolons

Examples:

```yaml
sign_off_options: |
  Max Muster
  Erika Beispiel
  John Doe
```

or:

```yaml
sign_off_options: "Max Muster, Erika Beispiel, John Doe"
```

## Weight field

The weight field is human-readable only and is not part of the QR payload.

Behavior:

- the UI only accepts digits
- the current label can enable or disable weight printing with a checkbox
- when printed, the add-on renders the value as `<number> kg`

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

Examples:

- `4` for a typical standard quiet zone
- `3` for a tighter border
- `2` for an even tighter border

### `qr_error_correction`

Allowed values:

- `L`
- `M`
- `Q`
- `H`

Higher levels give more redundancy and lower data capacity.

## Footer behavior

The footer is anchored to the physical bottom of the label.

Use `footer_bottom_margin_mm` to move it up from the edge.

Examples:

- `0` puts it at the bottom
- `3` moves it up by 3 mm
- `5` moves it up by 5 mm

## Web UI behavior

The add-on UI lets the operator:

- edit field 1, field 2, field 3, sign-off, weight, and footer text
- choose a sign-off from configured suggestions or type a custom one
- set copies
- enable or disable printing of field 2, field 3, weight, and footer for the current label
- inspect the live PNG preview
- open the full PNG preview
- inspect the generated ZPL before printing

Field 1 is always printed.

The sign-off prints whenever it contains a value.

The QR code still follows `qr_value_template` independently of whether field 2, field 3, weight, or the footer is hidden from the human-readable print output.

## Preview behavior

The PNG preview is generated from the same layout coordinates used for print generation and exported at 203 dpi.

That means:

- QR size, field placement, spacing, sign-off placement, weight placement, and footer placement match the print layout geometry
- the red border shows the full QR footprint including the configured quiet zone
- on-screen physical size still depends on browser zoom and monitor scaling

## JSON API

The add-on also exposes a simple JSON endpoint inside the add-on container:

`POST /api/print`

Example payload:

```json
{
  "text1": "250001",
  "text2": "EFH Huggentobbler Biel",
  "text3": "DE1",
  "sign_off": "Max Muster",
  "weight": "1250",
  "footer": "Ernst Fink AG, Schorenweg 144, 4585 Biezwil",
  "copies": 1,
  "print_text2": true,
  "print_text3": true,
  "print_weight": true,
  "print_footer": true
}
```

## Troubleshooting

### UI works but nothing prints

- confirm `printer_host` is the Zebra IP
- confirm the printer accepts raw printing on port `9100`
- confirm the printer is not paused or in error state
- confirm the media is calibrated on the printer

### Preview works but printing is clipped

- verify the stock size and orientation on the printer
- reduce `qr_size_mm` or add more top margin if needed
- remember the printer-safe width is 168 mm even if the configured label width is 170 mm

### Numeric fields reject input

- field 1 accepts digits only
- weight accepts digits only when you use it
- remove spaces, decimal separators, and unit text from the input itself

### Characters do not render as expected

The add-on renders text as graphics for consistent preview and print styling, but printer-side QR and ZPL behavior still depend on the Zebra firmware and fonts available in the environment.

## Version in this bundle

This synced documentation bundle corresponds to add-on version `0.1.17`.
