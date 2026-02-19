ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

ENV \
  PYTHONUNBUFFERED=1 \
  PIP_NO_CACHE_DIR=1 \
  PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps (Alpine uses apk, Debian/Ubuntu uses apt-get)
RUN set -eux; \
  if command -v apk >/dev/null 2>&1; then \
    apk add --no-cache \
      python3 \
      py3-pip \
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
      python3 \
      python3-pip \
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
RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir -r /app/requirements.txt

# App files
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
