#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S%z (%Z)'; }
log_info()  { echo "[$(ts)] [INFO ] $*"; }
log_error() { echo "[$(ts)] [ERROR] $*" >&2; }
log_diag()  { echo "[$(ts)] [DIAG ] $*"; }

# --- config arrays ---
IPS="$(bashio::config 'ip_list')"
NAMES="$(bashio::config 'name_list')"
TYPES="$(bashio::config 'type')"
TOUTS="$(bashio::config 'timeout_list')"

IFS=',' read -r -a IP_ARR   <<< "$IPS"
IFS=',' read -r -a NAME_ARR <<< "$NAMES"
IFS=',' read -r -a TYPE_ARR <<< "$TYPES"
IFS=',' read -r -a TOUT_ARR <<< "$TOUTS"

count="${#IP_ARR[@]}"
if [[ $count -eq 0 ]]; then
  log_error "No devices found in ip_list"
  exit 1
fi

log_info "Configured $count device(s). Starting parallel 5s polling loops..."

# --- logging lock & per-device log dir ---
LOG_DIR="/data/mkv_logs"
mkdir -p "$LOG_DIR"
# Open a lock file descriptor once; used with flock for atomic main-log blocks
LOCK_FILE="/tmp/mkv_mainlog.lock"
exec {LOGFD}> "$LOCK_FILE"  # FD number stored in $LOGFD

# --- device loop spawner ---
for ((i=0; i<count; i++)); do
  IP="${IP_ARR[$i]}"
  NAME="${NAME_ARR[$i]:-Device$i}"
  TYPE="${TYPE_ARR[$i]:-}"
  TIMEOUT="${TOUT_ARR[$i]:-5}"

  if [[ -z "$IP" || -z "$TYPE" ]]; then
    log_error "Skipping index $i: missing ip or type"
    continue
  fi

  DEVLOG="${LOG_DIR}/${NAME}.log"

  (
    # Slight stagger on start to avoid all devices aligning on the same second
    sleep $(( (RANDOM % 500) / 100 ))

    while true; do
      # Emit START line and command atomically in the main log
      flock -x "$LOGFD" bash -c '
        ts() { date "+%Y-%m-%d %H:%M:%S%z (%Z)"; }
        echo "[$(ts)] [INFO ] Polling '"'"'"$NAME"'"'"' ('"$IP"') with question set '"'"'"$TYPE"'"'"', timeout '"$TIMEOUT"'s"
        echo "[$(ts)] [DIAG ] Polling started for device '"'"'"$NAME"'"'"' ('"$IP"')"
      '

      # Run the poll; send its stdout/stderr to the per-device logfile
      start_ns=$(date +%s%N) || true
      python3 /atlas_copco_mkv.py \
        --question-set "$TYPE" \
        --controller-host "$IP" \
        --device-name "$NAME" \
        --timeout "$TIMEOUT" >>"$DEVLOG" 2>&1 || true
      end_ns=$(date +%s%N) || true
      dur_ms=$(( (end_ns - start_ns) / 1000000 ))

      # Emit FINISH line atomically so it always follows the START line
      flock -x "$LOGFD" bash -c '
        ts() { date "+%Y-%m-%d %H:%M:%S%z (%Z)"; }
        echo "[$(ts)] [DIAG ] Polling finished for device '"'"'"$NAME"'"'"' ('"$IP"') in '"$dur_ms"'ms"
      '

      # Maintain a 5-second poll rate per device
      sleep 5
    done
  ) &
done

# Wait for all background loops
wait
