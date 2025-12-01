#!/usr/bin/env bash
echo "[INFO] Panasonic F decoder starting..."

export HP_IP="${HP_IP}"
export HP_PORT="${HP_PORT}"
export MQTT_HOST="${MQTT_HOST}"
export MQTT_PORT="${MQTT_PORT}"
export MQTT_USER="${MQTT_USER}"
export MQTT_PASSWORD="${MQTT_PASSWORD}"
export MQTT_PREFIX="${MQTT_PREFIX}"

python3 /app/decoder.py
