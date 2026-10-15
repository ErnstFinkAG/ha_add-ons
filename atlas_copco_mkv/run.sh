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
INTERVALS="$(bashio::config 'interval_list')"
VERBOSES="$(bashio::config 'verbose_list')"

IFS=',' read -r -a IP_ARR   <<< "${IPS:-}"
IFS=',' read -r -a NAME_ARR <<< "${NAMES:-}"
IFS=',' read -r -a TYPE_ARR <<< "${TYPES:-}"
IFS=',' read -r -a TOUT_ARR <<< "${TOUTS:-}"
IFS=',' read -r -a INTV_ARR <<< "${INTERVALS:-}"
IFS=',' read -r -a VERB_ARR <<< "${VERBOSES:-}"

count="${#IP_ARR[@]}"
if [[ $count -eq 0 ]]; then
  log_error "No devices found in ip_list"
  exit 1
fi

log_info "Configured $count device(s). Starting parallel polling loops..."

for ((i=0; i<count; i++)); do
  IP="${IP_ARR[$i]}"
  NAME="${NAME_ARR[$i]:-Device$i}"
  TYPE="${TYPE_ARR[$i]:-}"
  TIMEOUT="${TOUT_ARR[$i]:-5}"
  INTERVAL="${INTV_ARR[$i]:-10}"
  VERBOSE="${VERB_ARR[$i]:-false}"
  INTERVAL="${INTERVAL//[[:space:]]/}"
  VERBOSE="${VERBOSE//[[:space:]]/}"
  if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [[ "$INTERVAL" -lt 1 ]]; then
    log_error "Invalid interval '${INTERVAL}' for index $i, using 10s"
    INTERVAL=10
  fi
  VERBOSE_ARGS=()
  if [[ "${VERBOSE,,}" == "true" ]]; then
    VERBOSE_ARGS=(--verbose)
  fi

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
      # Never let an error kill the loop; capture rc. The "|| rc=$?" is required:
      # with set -e a bare failing assignment would exit this subshell for good.
      rc=0
      poll_output="$(
        python3 /atlas_copco_mkv.py \
          --question-set "$TYPE" \
          --controller-host "$IP" \
          --device-name "$NAME" \
          --timeout "$TIMEOUT" \
          --interval "$INTERVAL" \
          "${VERBOSE_ARGS[@]}" 2>&1
      )" || rc=$?

      end_s=$(date +%s || echo "$start_s")
      dur_ms=$(( (end_s - start_s) * 1000 ))

      # Print START → RESULTS → FINISH as one atomic block
      lock_acquire
      {
        echo "[$(ts)] [INFO ] Polling '$NAME' ($IP) with question set '$TYPE', timeout ${TIMEOUT}s, interval ${INTERVAL}s"
        echo "[$(ts)] [DIAG ] Polling started for device '$NAME' ($IP)"
        printf '%s\n' "$poll_output"
        echo "[$(ts)] [DIAG ] Polling finished for device '$NAME' ($IP) in ${dur_ms}ms (rc=${rc})"
      }
      lock_release

      # Wait the configured interval before the next poll of this device
      sleep "$INTERVAL"
    done
  ) &
done

# keep the container alive; wait on all background loops
wait
