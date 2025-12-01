ARG BUILD_FROM=ghcr.io/hassio-addons/base:14.2.3
FROM ${BUILD_FROM}

# Use system Python + MQTT client
RUN apk add --no-cache python3 py3-paho-mqtt

WORKDIR /

COPY panasonic_f_decoder.py /panasonic_f_decoder.py
COPY run.sh /run.sh

RUN chmod a+x /run.sh

LABEL   io.hass.name="Panasonic F-Series Heatpump"   io.hass.slug="panasonic_f_heatpump"   io.hass.version="1.0.0.3"   io.hass.type="addon"   io.hass.arch="amd64|aarch64"

CMD ["/run.sh"]
