ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    py3-pip \
  && pip3 install --no-cache-dir --break-system-packages \
    flask==3.1.0 \
    qrcode==8.2 \
    pillow==11.1.0

WORKDIR /app
COPY app.py /app/app.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
