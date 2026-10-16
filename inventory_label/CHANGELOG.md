# Changelog

## 0.2.0

- Access check trusts only the source address. Before, any request with an `X-Ingress-Path` header was let in, so other containers on the Home Assistant network could use the add-on.
- `POST /api/print` can now be called by Home Assistant itself (for example from a `rest_command`). All other endpoints stay ingress-only.
- New option `field_editors`: list of Home Assistant users allowed to use the global field manager. Empty means everyone, as before.
- Field changes are saved atomically and one at a time, so simultaneous edits no longer overwrite each other and a restart during a save can no longer empty the field list.
- Docker base image follows the build architecture (fixes ARM builds). Dropped `armhf`, `armv7` and `i386`, which Home Assistant no longer supports.
- QR codes use a whole number of printer dots per module, so all modules print the same size.
- Label data for the printer is encoded much faster.
- Preview requests log at debug level instead of info.
- Added a watchdog so the Supervisor restarts the add-on if the web server stops.

## 0.1.91

- `panel_admin: false`: non-admin Home Assistant users can open the panel again (Core 2026.10 enforces the admin-only default).

## 0.1.90

This version keeps required-field validation for print, but preview no longer fails when required printed fields are still empty.

- preview and preview PNG now ignore required-field empties
- print and API print remain strict


This version speeds up printing from the web UI by sending print actions without reloading the full page.


This version mirrors the right text-box margin to the left side when no QR code is rendered, so the text block stays centered when left/right margins differ.

- the QR-code selection is now integrated into each configured field card next to the print checkbox

- configured fields section now appears above QR field selection in the main UI
This version replaces the separate QR/text gap profile setting with configurable text box side margins.

- each label profile now supports `text_box_margin_left_mm` and `text_box_margin_right_mm`
- the previous `qr_text_gap_mm` profile setting was removed
- text box side margins are used in both preview and print
- on rotated labels with QR, the left text box margin also acts as the gap between the QR block and the text box
- footer logo/text gap per field remains available in the field editor
- print shift X/Y per profile remains available for print-only positioning

It keeps the recent behavior changes:

- fields can switch render order inside the body or footer text block
- footer fields can render logos and text together, with text below the logos
- `qr_size_mm: 0` disables QR generation completely for that label profile
- there are no built-in default label profiles or fields anymore
- clicking a preview image prints that exact profile using the selected copy count

This version adds an add-on setting to show or hide the global field configuration section in the web UI.

- new `show_global_field_config` add-on option
- when disabled, the global field manager card is hidden from the web UI
- field edit endpoints are also blocked when the option is disabled
- existing global fields still remain active for preview, print, and API usage
