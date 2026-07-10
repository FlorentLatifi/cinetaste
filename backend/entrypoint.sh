#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-2}"

echo "Starting API on port ${PORT} (workers=${WORKERS})..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers "${WORKERS}" --proxy-headers --forwarded-allow-ips='*'
