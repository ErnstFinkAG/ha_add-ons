#!/usr/bin/with-contenv bashio

options="$(bashio::addon.options)"
if bashio::jq.exists "${options}" '.label_profiles_yaml'; then
  bashio::log.info "Removing legacy option label_profiles_yaml from add-on options"
  bashio::addon.option 'label_profiles_yaml'
fi

waitress-serve --listen=0.0.0.0:8099 app:APP
