ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# --- SYSTEM PACKAGES FOR OPENCV + PYTHON ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    cmake \
    ffmpeg \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libwebp-dev \
    libtiff-dev \
    libgl1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- PYTHON VENV ---
RUN python3 -m venv /opt/venv

# --- REQUIREMENTS ---
COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# --- APP FILES ---
COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.1.0" \
      io.hass.type="addon"

CMD [ "/run.sh" ]