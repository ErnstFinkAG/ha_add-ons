#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

HP_IP=$(bashio::config 'hp_ip')
HP_PORT=$(bashio::config 'hp_port')
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
MQTT_PREFIX=$(bashio::config 'mqtt_prefix')
POLL_INTERVAL=$(bashio::config 'poll_interval')
SCALE_WATER=$(bashio::config 'scale_water')
SCALE_INDOOR=$(bashio::config 'scale_indoor')
SCALE_TANK=$(bashio::config 'scale_tank')
SCALE_R1=$(bashio::config 'scale_r1')
LOG_LEVEL=$(bashio::config 'log_level')

export HP_IP HP_PORT MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASSWORD MQTT_PREFIX
export POLL_INTERVAL SCALE_WATER SCALE_INDOOR SCALE_TANK SCALE_R1 LOG_LEVEL

echo "[INFO] Starting Panasonic F decoder"
echo "[INFO] HP_IP=$HP_IP HP_PORT=$HP_PORT MQTT_HOST=$MQTT_HOST"

exec python3 /app/decoder.py
