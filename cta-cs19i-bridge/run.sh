#!/usr/bin/env bash
set -euo pipefail

OPTS="/data/options.json"

jget() {
python3 - "$1" <<'PY'
import json, sys
key = sys.argv[1]
with open("/data/options.json","r") as f:
    d = json.load(f)
v = d.get(key, "")
if isinstance(v, bool):
    print("true" if v else "false")
else:
    print(v)
PY
}

HOST="$(jget host)"
PORT="$(jget port)"
PASS="$(jget password)"
POLL="$(jget poll_interval)"
DELTA="$(jget demand_delta_c)"
PREFIX="$(jget mqtt_prefix)"
LOGP="$(jget log_pages)"
LOGC="$(jget log_changes_only)"

MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USERNAME:-${MQTT_USER:-}}"
MQTT_PASS="${MQTT_PASSWORD:-${MQTT_PASS:-}}"

ARGS=(
  --host "$HOST" --port "$PORT" --password "$PASS"
  --mqtt-host "$MQTT_HOST" --mqtt-port "$MQTT_PORT"
  --mqtt-user "$MQTT_USER" --mqtt-pass "$MQTT_PASS"
  --poll-interval "$POLL" --demand-delta "$DELTA"
  --mqtt-prefix "$PREFIX"
)

if [ "$LOGP" = "true" ]; then ARGS+=(--log-pages); fi
if [ "$LOGC" = "true" ]; then ARGS+=(--log-changes-only); fi

exec python3 /app/main.py "${ARGS[@]}"
