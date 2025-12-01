ARG BUILD_FROM=ghcr.io/home-assistant/amd64-addon-base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-paho-mqtt

COPY rootfs/ /
