FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /srv/sites \
    && chown 10001:10001 /srv/sites \
    && chmod 0755 /srv/sites
# /srv/sites: generated-site releases (M4B). Owned by the worker's uid so a fresh named volume
# mounted there inherits writable-by-worker, not-group/world-writable permissions. Only the
# worker mounts it read-write; Caddy mounts it read-only; the API does not mount it.

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY prompts ./prompts

USER appuser

EXPOSE 8000
