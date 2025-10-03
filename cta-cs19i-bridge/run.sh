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

HOSTNAME="$(jget hostname)"
CTRLP="$(jget controller_port)"
CTRLPASS="$(jget controller_password)"
POLL="$(jget poll_interval)"
DELTA="$(jget demand_delta_c)"

DISCOVERY="$(jget discovery_prefix)"
MQTTHOST="$(jget mqtt_host)"
MQTTPORT="$(jget mqtt_port)"
MQTTUSER="$(jget mqtt_username)"
MQTTPASS="$(jget mqtt_password)"
STATEBASE="$(jget state_base_topic)"

LOGP="$(jget log_pages)"
LOGC="$(jget log_changes_only)"

echo "[startup] MQTT -> host=${MQTTHOST} port=${MQTTPORT} user=${MQTTUSER}"
echo "[startup] Controller -> ${HOSTNAME}:${CTRLP}"

ARGS=(
  --host "$HOSTNAME" --port "$CTRLP" --password "$CTRLPASS"
  --mqtt-host "$MQTTHOST" --mqtt-port "$MQTTPORT"
  --mqtt-user "$MQTTUSER" --mqtt-pass "$MQTTPASS"
  --discovery-prefix "$DISCOVERY"
  --state-base-topic "$STATEBASE"
  --poll-interval "$POLL" --demand-delta "$DELTA"
)

if [ "$LOGP" = "true" ]; then ARGS+=(--log-pages); fi
if [ "$LOGC" = "true" ]; then ARGS+=(--log-changes-only); fi

exec python3 /app/main.py "${ARGS[@]}"
