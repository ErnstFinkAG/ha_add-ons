ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.21
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache \
    python3 \
    py3-pip \
  && pip3 install --no-cache-dir --break-system-packages \
    flask==3.1.2 \
    requests==2.32.5 \
    waitress==3.0.2

WORKDIR /app

COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static
COPY run.sh /run.sh

RUN chmod a+x /run.sh

EXPOSE 8098

CMD [ "/run.sh" ]
