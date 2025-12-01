#!/usr/bin/env bash
export HP_IP=$(bashio::config 'hp_ip')
export HP_PORT=$(bashio::config 'hp_port')
export MQTT_HOST=$(bashio::config 'mqtt_host')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password')
export MQTT_PREFIX=$(bashio::config 'mqtt_prefix')
export POLL_INTERVAL=$(bashio::config 'poll_interval')
export SCALE_WATER=$(bashio::config 'scale_water')
export SCALE_INDOOR=$(bashio::config 'scale_indoor')
export SCALE_TANK=$(bashio::config 'scale_tank')
export SCALE_R1=$(bashio::config 'scale_r1')
export LOG_LEVEL=$(bashio::config 'log_level')

echo "[INFO] Starting Panasonic F decoder add-on"
exec python3 /app/decoder.py
