ARG BUILD_FROM
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
        python3-venv \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- PYTHON VENV ---
RUN python3 -m venv /opt/venv

# --- INSTALL REQUIREMENTS ---
COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# --- COPY APP FILES ---
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
