ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-base:latest
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     PYTHONDONTWRITEBYTECODE=1     PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# System deps for Python and venv
RUN apk add --no-cache python3 py3-pip

# Prepare venv (avoids PEP 668 externally-managed error)
RUN python3 -m venv /opt/venv

# Copy requirements first to leverage layer caching
COPY requirements.txt /app/

# Install Python deps inside the venv
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# Copy application
COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Labels for HA
LABEL io.hass.version="0.1.0" io.hass.type="addon"

CMD [ "/run.sh" ]
