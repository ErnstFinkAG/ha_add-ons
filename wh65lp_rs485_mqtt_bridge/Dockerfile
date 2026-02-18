FROM python:3.12-alpine

COPY run.py /run.py
COPY requirements.txt /requirements.txt

RUN pip install --no-cache-dir -r /requirements.txt

CMD ["python3", "/run.py"]
