FROM python:3.12-alpine

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY hpm2mqtt /app/hpm2mqtt

WORKDIR /app
CMD ["python3", "-u", "-m", "hpm2mqtt", "/data/options.json"]
