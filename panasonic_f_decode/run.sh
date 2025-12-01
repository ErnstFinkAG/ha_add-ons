#!/bin/sh

OPTIONS_FILE="/data/options.json"

get_opt() {
  python3 - "$1" "$OPTIONS_FILE" << 'EOF'
import sys, json
key = sys.argv[1]
path = sys.argv[2]
with open(path, "r") as f:
    data = json.load(f)
v = data.get(key, "")
print(v)
EOF
}

export HP_IP=$(get_opt hp_ip)
export HP_PORT=$(get_opt hp_port)
export MQTT_HOST=$(get_opt mqtt_host)
export MQTT_PORT=$(get_opt mqtt_port)
export MQTT_USER=$(get_opt mqtt_user)
export MQTT_PASSWORD=$(get_opt mqtt_password)
export MQTT_PREFIX=$(get_opt mqtt_prefix)
export POLL_INTERVAL=$(get_opt poll_interval)
export SCALE_WATER=$(get_opt scale_water)
export SCALE_INDOOR=$(get_opt scale_indoor)
export SCALE_TANK=$(get_opt scale_tank)
export SCALE_R1=$(get_opt scale_r1)
export LOG_LEVEL=$(get_opt log_level)

echo "[INFO] Starting Panasonic F decoder"
echo "[INFO] HP_IP=$HP_IP HP_PORT=$HP_PORT MQTT_HOST=$MQTT_HOST"

exec python3 /app/decoder.py
