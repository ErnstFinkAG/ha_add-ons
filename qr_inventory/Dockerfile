ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-addon-base-python:3.11
FROM ${BUILD_FROM}

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:/opt/venv/bin:$PATH"

WORKDIR /app

# --- SYSTEM DEPENDENCIES FOR OPENCV ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- PYTHON VENV ---
RUN python3 -m venv /opt/venv

# --- REQUIREMENTS ---
COPY requirements.txt /app/
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/requirements.txt

# --- APP FILES ---
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD [ "/run.sh" ]