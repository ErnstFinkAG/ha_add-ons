#!/usr/bin/with-contenv bashio
set -euo pipefail

export VIRTUAL_ENV=/opt/venv
export PATH="${VIRTUAL_ENV}/bin:${PATH}"

bashio::log.info "Starting Inventory Label add-on"
exec python /app/app.py
