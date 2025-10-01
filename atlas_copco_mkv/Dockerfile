ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

# System deps
RUN apk add --no-cache python3 py3-pip bash curl         && python3 -m venv /opt/venv

# Ensure venv Python and pip are used
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /usr/src/app

# Install Python deps into the venv
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip         && pip install --no-cache-dir -r /tmp/requirements.txt         && rm -f /tmp/requirements.txt

# App files
COPY atlas_copco_parser.py ./
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
