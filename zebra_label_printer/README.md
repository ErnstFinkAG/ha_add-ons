# Zebra Label Printer

This Home Assistant add-on prints a large-format label to a networked Zebra printer using raw ZPL over TCP port 9100.

It is intended to be published in the GitHub repository:

`https://github.com/ErnstFinkAG/ha_add-ons`

## Repository layout

The repository root should look like this:

```text
ha_add-ons/
├── repository.json
└── zebra_label_printer/
    ├── app.py
    ├── config.yaml
    ├── Dockerfile
    ├── DOCS.md
    ├── README.md
    └── run.sh
```

## What it prints

- **Text string 1** as:
  - Human-readable text on the label
  - The QR-code payload
- **Text string 2** as additional human-readable text under the QR code

Example:

- Text string 1: `250001 - Test Project`
- Text string 2: `Element 1e`

## Printer assumptions

This add-on is designed for a **Zebra ZT420 / ZT421 at 203 dpi**.

Default layout:

- Label size: **170 mm × 305 mm**
- QR code size: **150 mm × 150 mm**
- Network printing: **TCP 9100**

### Important width note

A ZT420/ZT421 at 203 dpi has a **maximum print width of 168 mm / 1344 dots**.
If you leave the label width at 170 mm, the add-on will automatically clamp `^PW` to the printer-safe width.

## Publishing to GitHub

Upload these files to the repository so that `repository.json` is in the repository root and `zebra_label_printer` is a direct child folder.

Example branch structure:

```text
https://github.com/ErnstFinkAG/ha_add-ons
├── repository.json
└── zebra_label_printer/
```

## Installing in Home Assistant

1. Open **Settings → Add-ons → Add-on Store**.
2. Open the menu and choose **Repositories**.
3. Add this repository URL:

   ```text
   https://github.com/ErnstFinkAG/ha_add-ons
   ```

4. Install **Zebra Label Printer**.
5. Open the add-on configuration and set:
   - `printer_host`
   - `printer_port` (normally `9100`)
6. Start the add-on.
7. Open the add-on UI.

## Configuration

Example:

```yaml
printer_host: 192.168.1.60
printer_port: 9100
label_width_mm: 170
label_height_mm: 305
qr_size_mm: 150
top_margin_mm: 5
```

## Usage

Open the add-on UI and fill in:

- **Text string 1**
- **Text string 2**
- **Copies**

Then review the embedded PNG preview and click **Print label** when it looks correct.

Use **Open PNG preview** for a full-resolution export. The PNG is written at **203 dpi** so the image geometry matches the printer layout, although on-screen physical size still depends on browser zoom and monitor scaling.

## API

The add-on also exposes a simple JSON endpoint inside the container on `/api/print`:

```json
{
  "text1": "250001 - Test Project",
  "text2": "Element 1e",
  "copies": 1
}
```

## Troubleshooting

### Add-on does not appear in the store

- Confirm `repository.json` is in the repository root.
- Confirm `zebra_label_printer/config.yaml` exists.
- Confirm the GitHub repository is public or otherwise reachable by your Home Assistant instance.
- Remove and re-add the repository in Home Assistant if it was cached before the files were uploaded.

### Cannot connect to printer

- Confirm the printer IP is correct.
- Confirm port `9100` is enabled.
- Confirm the printer and Home Assistant are on reachable networks.
- Confirm no printer-side IP whitelist or TLS-only configuration is blocking raw socket printing.

### Label is shifted or clipped

- Calibrate media on the printer.
- Check the physical stock size and orientation.
- Reduce `top_margin_mm` or `qr_size_mm` if your stock or printer setup needs more margin.

### Unicode characters

The add-on sends UTF-8 ZPL (`^CI28`), but printed output still depends on the font support available on the printer.


## Notes

- Version 0.1.3 adds a PNG label preview rendered from the same layout coordinates as the ZPL output and embeds that preview in the add-on UI.
- Version 0.1.1 fixes Home Assistant Ingress form actions so **Print label** and **Preview ZPL** work correctly when opened via **Open Web UI**.


## Live preview

The PNG preview updates automatically when you change **Text string 1**, **Text string 2**, or **Copies** in the web UI.
