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

IFS=',' read -r -a IP_ARR   <<< "$IPS"
IFS=',' read -r -a NAME_ARR <<< "$NAMES"
IFS=',' read -r -a TYPE_ARR <<< "$TYPES"
IFS=',' read -r -a TOUT_ARR <<< "$TOUTS"

count="${#IP_ARR[@]}"

if [[ $count -eq 0 ]]; then
  log_error "No devices found in ip_list"
  exit 1
fi

log_info "Found $count device(s) in configuration"

for ((i=0; i<count; i++)); do
  IP="${IP_ARR[$i]}"
  NAME="${NAME_ARR[$i]:-Device$i}"
  TYPE="${TYPE_ARR[$i]:-}"
  TIMEOUT="${TOUT_ARR[$i]:-5}"

  if [[ -z "$IP" || -z "$TYPE" ]]; then
    log_error "Skipping index $i: missing ip or type"
    continue
  fi

  log_info "Polling '$NAME' ($IP) with question set '$TYPE', timeout ${TIMEOUT}s"

  python3 /atlas_copco_mkv.py \
    --question-set "$TYPE" \
    --controller-host "$IP" \
    --device-name "$NAME" \
    --timeout "$TIMEOUT"
done

log_info "Finished polling all devices"
