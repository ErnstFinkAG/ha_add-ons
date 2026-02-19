ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-base:latest
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# --- SYSTEMPAKETE FÜR OPENCV (ALPINE) ---
RUN apk add --no-cache \
    python3 \
    py3-pip \
    build-base \
    cmake \
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

# --- PYTHON VENV ---
RUN python3 -m venv /opt/venv

# --- REQUIREMENTS INSTALLIEREN ---
COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# --- APP KOPIEREN ---
COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.1.0" \
      io.hass.type="addon"

CMD [ "/run.sh" ]
