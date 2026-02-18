# WH65LP RS485 to MQTT Bridge

A Home Assistant Add-on for publishing WH65LP weather station data (via RS485/TCP) to MQTT with automatic Home Assistant Discovery.

---

## Features

- Reads live sensor data from your Misol WH65LP weather station (RS485/TCP).
- Publishes each sensor value to a configurable MQTT topic.
- Announces all sensors to Home Assistant using MQTT Discovery.
- Fully customizable MQTT topics and entity unique IDs (`unique_prefix`).
- You will need a running MQTT instance

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
  "mqtt_prefix": "mqtt_prefix",
  "discovery_prefix": "homeassistant",
  "unique_prefix": "your weatherstation",
  "ws_host": "10.80.24.101",
  "ws_port": 502
}
```

## WH65LP information

- Weatherstation sends Datapacket every 16 seconds.
- 21byte in hex

1st、2nd： 24 (identify tx type)

3rd、4th： 66 (security code)  - 6 -

5th、6th、7th： 65E (wind direction)  
misol                                                                                     
explanation: 65E(HEX) =0110, 0101,1110 (Binary)  
(Please refer to the Excel file.) 
Bit8=1, Bit 7=0, Bit 6=1, Bit 5=1, Bit 4=0, Bit 3=0, Bit 2=1, Bit 1=0, Bit 0=1, 
Wind direction is:B1 0110 0101= 357 (decimal)  
So, wind direction is: 357°

8th,9th,10th： 24D (Temperature)
(explanation:24D (HEX)= B010 0100 1101  = 589(Decimal)  
calculation： (589-400)/10=18.9  
so temperature is: 18.9℃

11th、12th：37 (Humidity)  
（Explanation:37(HEX)=55(D), so it is 55%)

13th、14 th：0D (wind speed)  
(explanation:  
00 (HEX) = B 0000 1101 
(Bit8=0, Bit 7=0, Bit 6=0, Bit 5=0, Bit 4=0, Bit 3=1, Bit 2=1, Bit 1=0, Bit 0=1,)  
So, the data is: B0 0000 1101 = 13 (D)  
calculation： 13/8*0.51=0.83 
So, wind speed is: 0.83 m/s.

15th、16th：03 (gust speed)  
(explanation: gust speed: 3 *0.51= 1.53 m/s )

17th-20 th： 0016 (accumulation rainfall)  
(explanation: accumulation rainfall: 22*0.254=5.59 mm )

21th-24th： 0000 (UV)  
(explanation: UV: 0 uW/cm2)

25th-30th： 00 5F 42 (LIGHT)  
(explanation: Light:24386/10=2438.6 LUX)

31th、32th：31    
CRC ( for the above 15 bytes, crc8, Polynomial_hex：31, data reverse:MSB first)

33th、34 th：4D  
checksum value (sum of previous 16 bytes)

35th-40th: 018F6A   (barometric pressure )  
(explanation: pressure:018F6A=102250, 102250/100=1022.50 hpa)

41th、42 th：FA   checksum value (sum for barometric pressure) 