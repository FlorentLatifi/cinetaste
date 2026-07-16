#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-2}"
# Only trust X-Forwarded-* from the platform proxy. Override via env if needed.
# Empty / unset → uvicorn default (127.0.0.1). Set to '*' only behind a known LB.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

echo "Starting API on port ${PORT} (workers=${WORKERS}, forwarded_allow_ips=${FORWARDED_ALLOW_IPS})..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS}"
