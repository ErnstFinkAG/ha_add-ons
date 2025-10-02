# Atlas Copco MKV (Home Assistant Add-on)

Poll Atlas Copco MK5s Touch controllers and print a parsed table to the add-on logs.

Supported Controllers Elektronikon MK5s
Supported Devices GA15VP13 and GA15VS23A.

Sends a Question String to the Controller via http post.
Gets Answer String back and decodes it depending on Device Type configured.

Polling is fixed at 5 second intervals.
Publishes sensors to mqtt with auto discover.

Sensors:

META_VP13: Dict[str, Any] = {
    "3002.01": {"Name": "Compressor Outlet", "Unit": "bar", "Encoding": "HiU16", "Calc": "HiU16/1000"},
    "3002.03": {"Name": "Element Outlet", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.05": {"Name": "Ambient Air", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.08": {"Name": "Controller Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3021.01": [
        {"Name": "Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3007.01": {"Name": "Running Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.03": {"Name": "Motor Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.04": {"Name": "Load Relay", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.05": {"Name": "VSD 1-20", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.06": {"Name": "VSD 20-40", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.07": {"Name": "VSD 40-60", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.08": {"Name": "VSD 60-80", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.09": {"Name": "VSD 80-100", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.0B": {"Name": "Fan Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0C": {"Name": "Accumulated Volume", "Unit": "m3", "Encoding": "UInt32", "Calc": "UInt32*1000"},
    "3007.0D": {"Name": "Module Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.0E": {"Name": "Emergency Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0F": {"Name": "Direct Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.14": {"Name": "Recirculation Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.15": {"Name": "Recirculation Failures", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.18": {"Name": "Low Load Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.22": {"Name": "Available Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.23": {"Name": "Unavailable Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.24": {"Name": "Emergency Stop Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3021.05": {"Name": "Flow", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32"},
    "3021.0A": {"Name": "Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3113.50": {"Name": "Service A 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.51": {"Name": "Service A 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.52": {"Name": "Service B 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.53": {"Name": "Service B 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.54": {"Name": "Machine Status", "Unit": "code", "Encoding": "UInt32", "Calc": "UInt32"},
}

META_VS23A: Dict[str, Any] = {
    "3002.01": {"Name": "Controller Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.24": {"Name": "Compressor Outlet", "Unit": "bar", "Encoding": "HiU16", "Calc": "HiU16/1000"},
    "3002.26": {"Name": "Ambient Air", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.27": {"Name": "Relative Humidity", "Unit": "%", "Encoding": "HiU16", "Calc": "HiU16"},
    "3002.2A": {"Name": "Element Outlet", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.66": {"Name": "Aftercooler drain PCB Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3021.01": [
        {"Name": "Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3022.01": [
        {"Name": "Fan Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Fan Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3007.01": {"Name": "Running Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.03": {"Name": "Motor Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.04": {"Name": "Load Relay", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.05": {"Name": "VSD 1-20", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.06": {"Name": "VSD 20-40", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.07": {"Name": "VSD 40-60", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.08": {"Name": "VSD 60-80", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.09": {"Name": "VSD 80-100", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.0B": {"Name": "Fan Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0C": {"Name": "Accumulated Volume", "Unit": "m3", "Encoding": "UInt32", "Calc": "UInt32*1000"},
    "3007.0D": {"Name": "Module Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.0E": {"Name": "Emergency Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0F": {"Name": "Direct Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.17": {"Name": "Recirculation Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.18": {"Name": "Recirculation Failures", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.1B": {"Name": "Low Load Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.25": {"Name": "Available Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.26": {"Name": "Unavailable Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.27": {"Name": "Emergency Stop Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.43": {"Name": "Display Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.4C": {"Name": "Boostflow Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.4D": {"Name": "Boostflow Activations", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.54": {"Name": "Emergency Stops During Running", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.55": {"Name": "Drain 1 Operation Time", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.56": {"Name": "Drain 1 number of switching actions", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.57": {"Name": "Drain 1 number of manual drainings", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3021.05": {"Name": "Flow", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32"},
    "3021.0A": {"Name": "Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3022.0A": {"Name": "Fan Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3113.50": {"Name": "Service A 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.51": {"Name": "Service A 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.52": {"Name": "Service B 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.53": {"Name": "Service B 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.54": {"Name": "Service D 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.55": {"Name": "Service D 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.56": {"Name": "Machine Status", "Unit": "code", "Encoding": "UInt32", "Calc": "UInt32"},
}