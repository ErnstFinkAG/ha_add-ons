FROM python:3.11-slim
RUN pip install paho-mqtt
WORKDIR /app
COPY decoder.py /app/
COPY run.sh /app/
RUN chmod +x /app/run.sh
CMD ["/app/run.sh"]
