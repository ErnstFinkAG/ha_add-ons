ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-base:latest
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     PYTHONDONTWRITEBYTECODE=1     PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apk add --no-cache python3 py3-pip
RUN python3 -m venv /opt/venv

COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.2.3" io.hass.type="addon"

CMD [ "/run.sh" ]
