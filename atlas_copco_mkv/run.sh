#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S%z (%Z)'; }
log_info()  { echo "[$(ts)] [INFO ] $*"; }
log_error() { echo "[$(ts)] [ERROR] $*" >&2; }
log_diag()  { echo "[$(ts)] [DIAG ] $*"; }

# --- simple portable lock using a directory (no external flock needed) ---
LOCK_DIR="/tmp/mkv_mainlog.lockdir"
lock_acquire() {
  # spin until we can create the directory
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    # tiny sleep to reduce contention
    sleep 0.05
  done
}
lock_release() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

# --- read config into arrays ---
IPS="$(bashio::config 'ip_list')"
NAMES="$(bashio::config 'name_list')"
TYPES="$(bashio::config 'type')"
TOUTS="$(bashio::config 'timeout_list')"

# NEW: MQTT settings
MQTT_HOST="$(bashio::config 'mqtt_host')"
MQTT_PORT="$(bashio::config 'mqtt_port')"
MQTT_USER="$(bashio::config 'mqtt_username')"
# fallback to mqtt_user if provided
: "${MQTT_USER:=$(bashio::config 'mqtt_user')}"
MQTT_PASS="$(bashio::config 'mqtt_password')"
DISC_PREFIX="$(bashio::config 'discovery_prefix')"
STATE_BASE="$(bashio::config 'state_base_topic')"

IFS=',' read -r -a IP_ARR   <<< "${IPS:-}"
IFS=',' read -r -a NAME_ARR <<< "${NAMES:-}"
IFS=',' read -r -a TYPE_ARR <<< "${TYPES:-}"
IFS=',' read -r -a TOUT_ARR <<< "${TOUTS:-}"

count="${#IP_ARR[@]}"
if [[ $count -eq 0 ]]; then
  log_error "No devices found in ip_list"
  exit 1
fi

log_info "Configured $count device(s). Starting parallel 5s polling loops..."

for ((i=0; i<count; i++)); do
  IP="${IP_ARR[$i]}"
  NAME="${NAME_ARR[$i]:-Device$i}"
  TYPE="${TYPE_ARR[$i]:-}"
  TIMEOUT="${TOUT_ARR[$i]:-5}"

  if [[ -z "$IP" || -z "$TYPE" ]]; then
    log_error "Skipping index $i: missing ip or type (ip='${IP:-}', type='${TYPE:-}')"
    continue
  fi

  (
    # small stagger (0–3s) so all devices don't align on the same second
    sleep $(( RANDOM % 4 ))

    while true; do
      start_s=$(date +%s || echo 0)

      # Run the poll and CAPTURE output so we can print it as one block.
      # Never let an error kill the loop; capture rc.
      poll_output="$(
        python3 /atlas_copco_mkv.py \
          --question-set "$TYPE" \
          --controller-host "$IP" \
          --device-name "$NAME" \
          --timeout "$TIMEOUT" 2>&1
      )"
      rc=$?

      end_s=$(date +%s || echo "$start_s")
      dur_ms=$(( (end_s - start_s) * 1000 ))

      # Print START → RESULTS → FINISH as one atomic block
      lock_acquire
      {
        echo "[$(ts)] [INFO ] Polling '$NAME' ($IP) with question set '$TYPE', timeout ${TIMEOUT}s"
        echo "[$(ts)] [DIAG ] Polling started for device '$NAME' ($IP)"
        printf '%s\n' "$poll_output"
        echo "[$(ts)] [DIAG ] Polling finished for device '$NAME' ($IP) in ${dur_ms}ms (rc=${rc})"
      }
      lock_release

      # Maintain a 5-second poll interval per device
      sleep 5
    done
  ) &
done

# keep the container alive; wait on all background loops
wait
