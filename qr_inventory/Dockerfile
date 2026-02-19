ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-addon-base:14.1.1
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# SYSTEM PACKAGES (ALPINE)
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-virtualenv \
    build-base \
    linux-headers \
    jpeg-dev \
    zlib-dev \
    libpng-dev \
    libwebp-dev \
    tiff-dev \
    ffmpeg \
    mesa-gl \
    libstdc++ \
    libgcc

# PYTHON VENV
RUN python3 -m venv /opt/venv

# REQUIREMENTS
COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# APP FILES
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD [ "/run.sh" ]