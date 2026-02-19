ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

ENV \
  PYTHONUNBUFFERED=1 \
  PIP_NO_CACHE_DIR=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  VIRTUAL_ENV=/opt/venv \
  PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

WORKDIR /app

RUN set -eux; \
  if command -v apk >/dev/null 2>&1; then \
    apk add --no-cache \
      ca-certificates \
      python3 \
      py3-pip \
      py3-virtualenv \
      py3-opencv \
      py3-numpy \
      ffmpeg \
      mesa-gl \
      glib \
      libsm \
      libxext \
      libxrender \
      libstdc++ \
      ; \
  elif command -v apt-get >/dev/null 2>&1; then \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      python3 \
      python3-venv \
      python3-pip \
      python3-opencv \
      python3-numpy \
      ffmpeg \
      libgl1 \
      libglib2.0-0 \
      libsm6 \
      libxext6 \
      libxrender1 \
      ; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
  else \
    echo "No supported package manager found (apk/apt-get)"; \
    exit 1; \
  fi

COPY requirements.txt /app/requirements.txt

RUN python3 -m venv /opt/venv --system-site-packages && \
    /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
