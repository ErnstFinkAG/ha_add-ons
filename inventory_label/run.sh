#!/usr/bin/with-contenv bashio
set -euo pipefail

bashio::log.info "Starting Inventory Label add-on"
python3 /app/app.py
