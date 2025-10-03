#!/usr/bin/env bash
set -euo pipefail

OPTS="/data/options.json"

jget() {
python3 - "$1" <<'PY'
import json, sys
key = sys.argv[1]
with open("/data/options.json","r") as f:
    d = json.load(f)
print(d.get(key, ""))
PY
}

HOST="$(jget host)"
PORT="$(jget port)"
PASS="$(jget password)"
POLL="$(jget poll_interval)"
DELTA="$(jget demand_delta_c)"
PREFIX="$(jget mqtt_prefix)"

# MQTT creds injected by Supervisor when 'services: [mqtt:need]' is set
MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USERNAME:-${MQTT_USER:-}}"
MQTT_PASS="${MQTT_PASSWORD:-${MQTT_PASS:-}}"



# Optional logging of values like PowerShell script
LOG_VALUES="$(jget log_values)"
if [ "$LOG_VALUES" = "true" ]; then
  EXTRA="--log-values"
else
  EXTRA=""
fi

# shellcheck disable=SC2086
  --host "$HOST" --port "$PORT" --password "$PASS" \  --mqtt-host "$MQTT_HOST" --mqtt-port "$MQTT_PORT" \  --mqtt-user "$MQTT_USER" --mqtt-pass "$MQTT_PASS" \  --poll-interval "$POLL" --demand-delta "$DELTA" \  --mqtt-prefix "$PREFIX" $EXTRA