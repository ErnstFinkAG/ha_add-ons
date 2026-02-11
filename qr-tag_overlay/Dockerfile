ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 py3-pip \
    zbar \
    ttf-dejavu \
    jpeg-dev zlib-dev

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY app/server.py /app/server.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]