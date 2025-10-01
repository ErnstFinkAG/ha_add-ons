FROM ghcr.io/home-assistant/amd64-base:latest

# Keep Python tools in a virtualenv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python and create venv
RUN apk add --no-cache python3 py3-pip bash curl && python3 -m venv /opt/venv

# Work dir for the app
WORKDIR /usr/src/app

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# Copy app code and runtime
COPY atlas_copco_parser.py ./
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Start the add-on
CMD ["/run.sh"]
