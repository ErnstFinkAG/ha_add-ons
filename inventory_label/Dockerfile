ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    py3-pip

WORKDIR /app
COPY rootfs/app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY rootfs/app /app
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
