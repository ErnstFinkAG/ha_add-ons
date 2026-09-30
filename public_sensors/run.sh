#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Public Sensors on port 8098"
exec waitress-serve --listen=0.0.0.0:8098 --threads=4 app:APP
