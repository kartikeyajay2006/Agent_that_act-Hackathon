# Shared image recipe for the demo services. SERVICE_DIR picks which app to run.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG SERVICE_DIR
COPY common/ common/
COPY ${SERVICE_DIR}/app.py app.py

RUN useradd --uid 10001 --no-create-home appuser
USER appuser
