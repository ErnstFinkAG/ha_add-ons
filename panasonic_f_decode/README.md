# Panasonic F-Series Heatpump Decoder Add-on

This add-on reads Panasonic Aquarea F-series CN-CNT debug output over TCP
(from a CH9121 Ethernet-serial bridge), decodes it, and publishes many
operational and diagnostic values to MQTT with Home Assistant auto-discovery.

It is implemented as a proper HA add-on using:
- ghcr.io/home-assistant/amd64-addon-base
- s6-overlay services
- bashio for configuration
