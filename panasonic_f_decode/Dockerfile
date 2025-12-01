FROM python:3.11-slim

RUN pip install paho-mqtt

WORKDIR /app
COPY decoder.py /app/decoder.py
COPY run.sh /app/run.sh

RUN chmod a+x /app/run.sh

CMD ["/app/run.sh"]
