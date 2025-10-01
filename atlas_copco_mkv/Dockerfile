ARG BUILD_FROM=ghcr.io/hassio-addons/base-python:13.2.2
FROM $BUILD_FROM

# Copy files
COPY run.sh /run.sh
COPY atlas_copco_mkv.py /atlas_copco_mkv.py

# Install Python dependencies
RUN pip3 install --no-cache-dir requests paho-mqtt

RUN chmod a+x /run.sh

CMD ["/run.sh"]
