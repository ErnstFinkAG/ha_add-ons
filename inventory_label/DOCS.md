# Inventory Label

Home Assistant add-on for printing large QR-code labels to a networked Zebra ZT420/ZT421.

## Add-on config

Configure one or more label profiles in the add-on **Configuration** tab. On a fresh install, add your first profile there before you start using the web UI. Set `qr_size_mm: 0` on a profile when that label should not generate a QR code at all. Use `print_shift_x_mm` and `print_shift_y_mm` to move only the printed output in mm without changing preview. Use `text_box_margin_left_mm` and `text_box_margin_right_mm` to control the text box side margins. On rotated labels with QR, the left text box margin also acts as the gap between the QR block and the text box.

Example:

```yaml
ui_language: de
show_global_field_config: true
field_editors: []
label_profiles:
  - id: standard
    name: Standard
    printer_host: ""
    printer_dpi: 203
    label_width_mm: 170
    label_height_mm: 305
    qr_size_mm: 170
    top_margin_mm: 0
    text_box_margin_left_mm: 8
    text_box_margin_right_mm: 8
    footer_bottom_margin_mm: 0
    print_shift_x_mm: 0
    print_shift_y_mm: 0
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
    text_box_margin_left_mm: 8
    text_box_margin_right_mm: 8
    footer_bottom_margin_mm: 0
    print_shift_x_mm: 0
    print_shift_y_mm: 0
    print_rotation_degrees: 90
    qr_quiet_zone_modules: 4
    qr_error_correction: M
    show_in_preview: false
```

`show_in_preview` defaults to `false` when omitted.

The add-on now starts with an empty `label_profiles` list by default, so nothing is pre-created for you.

`show_global_field_config` defaults to `true`. Set it to `false` when users should still print with the shared global fields but should not see or edit the field manager in the web UI.

`field_editors` limits the field manager to some users. List Home Assistant user names (login name or display name, not case sensitive). When the list is empty, every user who can open the panel can edit fields. Users not on the list can still print.

```yaml
field_editors:
  - localadmin
  - nino
```

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
- `footer_logo_text_gap_mm`
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
- manage the global fields in one shared field section when `show_global_field_config` is enabled
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

Home Assistant can call this endpoint directly, for example with a `rest_command`. Use the add-on hostname shown on the add-on's **Info** page (the part before `:8099` in the URL, it looks like `xxxxxxxx-inventory-label`):

```yaml
rest_command:
  print_inventory_label:
    url: "http://xxxxxxxx-inventory-label:8099/api/print"
    method: POST
    content_type: "application/json"
    payload: >-
      {"profile_id": "standard", "qr_field_ids": ["project_no"], "field_values": {"project_no": "{{ project_no }}"}}
```

Only Home Assistant itself (and the ingress panel) can reach the add-on; other add-ons and other devices get 403.
