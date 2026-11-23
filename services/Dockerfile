FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home phoenix \
    && mkdir /data \
    && chown phoenix:phoenix /data
COPY brain ./brain
COPY version_switch ./version_switch

USER phoenix
