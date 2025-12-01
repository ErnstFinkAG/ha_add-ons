ARG BUILD_FROM=ghcr.io/hassio-addons/base:14.2.3
FROM ${BUILD_FROM}

# Install Python + MQTT
RUN apk add --no-cache python3 py3-pip
RUN pip install paho-mqtt

WORKDIR /app
COPY decoder.py /app/
COPY run.sh /app/
RUN chmod +x /app/run.sh

CMD [ "/app/run.sh" ]
