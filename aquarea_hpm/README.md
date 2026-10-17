# Aquarea HPM Bridge

Turns the web interface of a Panasonic Aquarea Heat Pump Manager (PAW-HPM / HPM1 / HPM2,
named like `HPM-800F22` on the network) into Home Assistant entities via MQTT discovery:

- every value shown in the HPM web interface becomes a sensor;
- every value the HPM lets you change becomes a `number` (with the HPM's min/max) or a `select`
  (with its options);
- a **Rescan values** button and a connection sensor.

Requires an MQTT broker (Mosquitto add-on) and the HPM reachable on the LAN.
See [DOCS.md](DOCS.md) for options and how it works.
