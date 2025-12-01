#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S%z (%Z)'; }
log_info()  { echo "[$(ts)] [INFO ] $*"; }
log_error() { echo "[$(ts)] [ERROR] $*" >&2; }
log_diag()  { echo "[$(ts)] [DIAG ] $*"; }

HP_IP="$(bashio::config 'hp_ip')"
HP_PORT="$(bashio::config 'hp_port')"
DEVICE_NAME="$(bashio::config 'device_name')"
POLL_INTERVAL="$(bashio::config 'poll_interval')"
CONNECT_TIMEOUT="$(bashio::config 'connect_timeout')"
MQTT_HOST="$(bashio::config 'mqtt_host')"
MQTT_PORT="$(bashio::config 'mqtt_port')"
MQTT_USER="$(bashio::config 'mqtt_username')"
MQTT_PASS="$(bashio::config 'mqtt_password')"
MQTT_DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
MQTT_STATE_BASE="$(bashio::config 'state_base_topic')"

log_info "Starting Panasonic F decoder"
log_info "HP_IP=${HP_IP} HP_PORT=${HP_PORT} MQTT_HOST=${MQTT_HOST}:${MQTT_PORT} interval=${POLL_INTERVAL}s timeout=${CONNECT_TIMEOUT}s"

while true; do
  start_s=$(date +%s || echo 0)

  output="$(
    set +e
    python3 /panasonic_f_decoder.py       --hp-ip "${HP_IP}"       --hp-port "${HP_PORT}"       --device-name "${DEVICE_NAME}"       --timeout "${CONNECT_TIMEOUT}"       --mqtt-host "${MQTT_HOST}"       --mqtt-port "${MQTT_PORT}"       --mqtt-username "${MQTT_USER}"       --mqtt-password "${MQTT_PASS}"       --discovery-prefix "${MQTT_DISCOVERY_PREFIX}"       --state-base-topic "${MQTT_STATE_BASE}" 2>&1
    echo $? > /tmp/panasonic_f_rc.$$
  )"
  rc=$(cat /tmp/panasonic_f_rc.$$ || echo 1; rm -f /tmp/panasonic_f_rc.$$ || true)

  end_s=$(date +%s || echo "$start_s")
  dur_ms=$(( (end_s - start_s) * 1000 ))

  log_diag "Poll finished in ${dur_ms}ms (rc=${rc})"
  printf '%s
' "$output"

  sleep "${POLL_INTERVAL}"
done
