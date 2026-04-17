FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tgmsgcopy.py ./
COPY tgforwarder ./tgforwarder

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "tgmsgcopy.py"]

