# Home Assistant add-on base image (Alpine).
# Newer Supervisor versions only pass BUILD_ARCH, not BUILD_FROM, so derive the
# base image from the architecture here.
ARG BUILD_ARCH=amd64
ARG BUILD_FROM=ghcr.io/home-assistant/${BUILD_ARCH}-base:latest
FROM ${BUILD_FROM}

# Install Python and needed libs via Alpine packages (no pip3 required)
RUN apk add --no-cache python3 py3-requests py3-paho-mqtt

# Copy files
COPY run.sh /run.sh
COPY atlas_copco_mkv.py /atlas_copco_mkv.py

# Ensure entrypoint is executable
RUN chmod a+x /run.sh

# s6-overlay will invoke /run.sh
CMD ["/run.sh"]
