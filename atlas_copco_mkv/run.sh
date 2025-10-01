#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S%z (%Z)'; }
log_info()  { echo "[$(ts)] [INFO ] $*"; }
log_error() { echo "[$(ts)] [ERROR] $*" >&2; }

log_info "Starting Atlas Copco MKV add-on"

IPS="$(bashio::config 'ip_list')"
NAMES="$(bashio::config 'name_list')"
TYPES="$(bashio::config 'type')"
TOUTS="$(bashio::config 'timeout_list')"

# Use the first entry from each CSV as a minimal working example.
# (We can extend to multi-device loop later.)
IFS=',' read -r IP1 _ <<< "$IPS"
IFS=',' read -r NAME1 _ <<< "$NAMES"
IFS=',' read -r TYPE1 _ <<< "$TYPES"
IFS=',' read -r TOUT1 _ <<< "$TOUTS"

if [[ -z "${IP1:-}" || -z "${TYPE1:-}" ]]; then
  log_error "ip_list and type must have at least one entry"
  exit 1
fi

TIMEOUT="${TOUT1:-5}"

log_info "Polling '$NAME1' ($IP1) with question set '$TYPE1', timeout ${TIMEOUT}s"
log_info "Command: python3 /atlas_copco_mkv.py --question-set \"$TYPE1\" --controller-host \"$IP1\" --device-name \"$NAME1\" --timeout \"$TIMEOUT\""

exec python3 /atlas_copco_mkv.py \
  --question-set "$TYPE1" \
  --controller-host "$IP1" \
  --device-name "$NAME1" \
  --timeout "$TIMEOUT"
