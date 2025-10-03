ARG BUILD_FROM=ghcr.io/hassio-addons/base:15.0.5
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt /app/
RUN apk add --no-cache python3 py3-pip &&     pip3 install -r /app/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.1.0" io.hass.type="addon"

CMD [ "/run.sh" ]
