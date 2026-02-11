# Default is only for linting; Supervisor will override BUILD_FROM
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    ffmpeg \
    zbar \
    ttf-dejavu \
    jpeg-dev \
    zlib-dev \
    build-base \
    libffi-dev \
    openssl-dev

RUN python3 -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY app/server.py /app/server.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
