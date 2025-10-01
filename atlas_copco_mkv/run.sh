#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S%z (%Z)'; }
log_info()  { echo "[$(ts)] [INFO ] $*"; }
log_error() { echo "[$(ts)] [ERROR] $*" >&2; }
log_diag()  { echo "[$(ts)] [DIAG ] $*"; }

# --- read config into arrays ---
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

# Use a single lock file so we can print log blocks atomically
LOCK_FILE="/tmp/mkv_mainlog.lock"
: > "$LOCK_FILE" || true

for ((i=0; i<count; i++)); do
  IP="${IP_ARR[$i]}"
  NAME="${NAME_ARR[$i]:-Device$i}"
  TYPE="${TYPE_ARR[$i]:-}"
  TIMEOUT="${TOUT_ARR[$i]:-5}"

  if [[ -z "$IP" || -z "$TYPE" ]]; then
    log_error "Skipping index $i: missing ip or type (ip='$IP', type='$TYPE')"
    continue
  fi

  (
    # slight random stagger to avoid exact alignment
    sleep $(( (RANDOM % 400) / 100 ))

    while true; do
      start_ns=$(date +%s%N || echo 0)

      # run the poll and CAPTURE output so we can print it as one block
      poll_output="$(
        pytho
