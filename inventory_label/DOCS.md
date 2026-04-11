# Inventory Label

## What this rewrite does

This add-on defines two top-level configuration objects:

- `labelprofiles`: per-printer label settings
- `labelfields`: shared fields applied to every profile

The service starts a small web page through ingress that shows:

- the raw `options.json`
- the normalized configuration
- configuration errors, if any

## Important note about PNG uploads

The Home Assistant add-on configuration schema supports typed values, nested arrays and nested dictionaries, but it does not provide a native file upload field in the add-on config form.

Because of that, logo images are handled through `logo_path`.

Recommended locations for PNG files:

- `/config/logos/...`
- `/share/...`

Then reference the file from `logo_path`.

## Example configuration

```yaml
labelprofiles:
  - id: standard170x305
    name: Standard 170 x 305
    printerhost: 10.50.20.12
    printer_port: 9100
    printer_dpi: 300
    label_width_mm: 170
    label_length_mm: 305
    top_margin_mm: 0
    left_margin_mm: 0
    print_rotation: 0
    qr_code_quietzone_modules: 3
    qr_code_error_correction: M
    show_in_preview: true

labelfields:
  - id: assetnumber
    name: Asset Number
    fontsize: 18
    default_value: ""
    valuelist: []
    logo: false
    logo_path: ""
    heading: Inventory
    fontfamily: Arial
    position: text
    max_lines: 1
    footer_margin_bottom: 0
    bold: true
    italic: false
    underline: false
    print_by_default: true
    numbers_only: true
    append_current_date: false
    default_for_rendering_qr_code: true

  - id: companylogo
    name: Company Logo
    fontsize: 12
    default_value: ""
    valuelist: []
    logo: true
    logo_path: logos/company.png
    heading: ""
    fontfamily: Arial
    position: footer
    max_lines: 1
    footer_margin_bottom: 2
    bold: false
    italic: false
    underline: false
    print_by_default: true
    numbers_only: false
    append_current_date: false
    default_for_rendering_qr_code: false
```

## Current validation rules

- profile IDs must be unique
- field IDs must be unique
- IDs must use lowercase letters and digits only
- only one field may be the default QR value source
- `numbers_only` fields may only contain numeric defaults and numeric value lists
- non-logo fields automatically clear `logo_path`
