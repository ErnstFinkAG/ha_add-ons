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
    su-exec \
  && addgroup -S publicsensor \
  && adduser -S -D -H -G publicsensor publicsensor \
  && pip3 install --no-cache-dir --break-system-packages \
    flask==3.1.2 \
    requests==2.32.5 \
    waitress==3.0.2 \
    PyYAML==6.0.2

WORKDIR /app

COPY collector.py /app/collector.py
COPY web.py /app/web.py
COPY templates /app/templates
COPY static /app/static
COPY run.sh /run.sh

RUN chmod a+x /run.sh \
  && chmod -R a+rX /app

EXPOSE 8098

CMD [ "/run.sh" ]
