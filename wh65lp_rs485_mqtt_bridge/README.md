# WH65LP RS485 to MQTT Bridge

A Home Assistant Add-on for publishing WH65LP weather station data (via RS485/TCP) to MQTT with automatic Home Assistant Discovery.

---

## Features

- Reads live sensor data from your Misol WH65LP weather station (RS485/TCP).
- Publishes each sensor value to a configurable MQTT topic.
- Announces all sensors to Home Assistant using MQTT Discovery.
- Fully customizable MQTT topics and entity unique IDs (`unique_prefix`).
- Runs as a Home Assistant add-on with zero extra dependencies.

---

## Installation

1. **Add this repo to Home Assistant:**
   - Go to *Settings > Add-ons > Add-on Store > ... > Repositories*.
   - Add your repo URL (e.g. `https://github.com/ErnstFinkAG/ha_addons`).

2. **Install the add-on:**
   - Find `WH65LP RS485 to MQTT` in the add-on store.
   - Click Install.

3. **Configure the add-on:**
   - Set all MQTT and weather station connection parameters under **Configuration**.
   - The most important fields are:
     - `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_pass`: MQTT broker connection details.
     - `mqtt_prefix`: The prefix for MQTT topics (e.g. `myweatherstation`).
     - `discovery_prefix`: Usually **set to `homeassistant`** for Home Assistant MQTT Discovery.
     - `unique_prefix`: Must be set! Used as entity and unique ID prefix (e.g. `myweatherstation1`).
     - `ws_host`, `ws_port`: IP/port of your WH65LP station (or RS485 gateway).
     - `packet_size`: Normally `25`.

4. **Start the add-on.**

5. **Check Home Assistant Entities:**
   - After startup, Home Assistant should auto-discover all sensors.
   - Go to *Settings > Devices & Services > Entities* and search for your prefix (e.g. `sensor.myweatherstation1_temperature_c`).

---

## Example Add-on Configuration

```json
{
  "mqtt_host": "localhost",
  "mqtt_port": 1883,
  "mqtt_user": "mqtt_user",
  "mqtt_pass": "mqtt_password",
  "mqtt_prefix": "efimmo-bw-b2-f4-ws1",
  "discovery_prefix": "homeassistant",
  "unique_prefix": "efimmo-bw-b2-f4-ws1",
  "ws_host": "10.80.24.101",
  "ws_port": 502,
  "packet_size": 25
}
