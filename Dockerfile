FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANNOTATIONS_DIR=/var/data/annotations \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /var/data/annotations

EXPOSE 8000
# One instance, a couple of workers + threads. File locks make this safe on a
# single machine; do NOT scale to multiple machines/instances.
CMD gunicorn --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:${PORT} app:app
