FROM ghcr.io/home-assistant/base:latest


RUN apk add --no-cache \
    python3 \
    py3-pip \
    ttf-dejavu \
  && pip3 install --no-cache-dir --break-system-packages \
    flask==3.1.0 \
    waitress==3.0.2 \
    qrcode==8.2 \
    pillow==11.1.0 \
    pyyaml==6.0.2

WORKDIR /app
COPY app.py /app/app.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]