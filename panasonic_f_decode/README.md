# Panasonic F-Series Heatpump Add-on

This add-on decodes Panasonic Aquarea F-Series heatpump data from a CH9121 Ethernet-Serial adapter
and publishes decoded values to MQTT with Home Assistant auto-discovery.

It exposes:
- Mode
- Water / indoor / tank / refrigerant / outdoor temperatures (scaled by config)
- Flow and tank setpoints
- Pump RPM, duty, compressor request
- Compressor frequency and heating/cooling/tank power
- Raw debug lines for R_MODE, CMP_REQ, DIFF, C_SET, ER_CODE
