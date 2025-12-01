ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-paho-mqtt

WORKDIR /app
COPY decoder.py /app/
COPY run.sh /app/
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
