ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

# Hard-disable s6-overlay so it can never run
RUN rm -rf /init /etc/services.d /etc/s6* /run/s6 || true

# Install python + mqtt client
RUN apk add --no-cache python3 py3-paho-mqtt

WORKDIR /app
COPY decoder.py /app/
COPY run.sh /app/
RUN chmod +x /app/run.sh

ENTRYPOINT ["/app/run.sh"]
CMD []
