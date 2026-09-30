#!/usr/bin/with-contenv bashio
set -eu

mkdir -p /run/public-sensors
chmod 0755 /run/public-sensors

# Keep private configuration inaccessible to the unprivileged public process.
chmod 0700 /data 2>/dev/null || true
chmod 0600 /data/options.json 2>/dev/null || true

bashio::log.info "Starting Home Assistant collector"
python3 -u /app/collector.py &
COLLECTOR_PID=$!

sleep 1
if ! kill -0 "${COLLECTOR_PID}" 2>/dev/null; then
    bashio::log.error "Collector exited during startup"
    exit 1
fi

# The Internet-facing process must not inherit the Home Assistant token.
unset SUPERVISOR_TOKEN

bashio::log.info "Starting credential-free Public Sensors web server on port 8098"
exec su-exec publicsensor:publicsensor \
    waitress-serve --listen=0.0.0.0:8098 --threads=4 web:APP
