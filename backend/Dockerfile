# ---- build ---------------------------------------------------------------
# Wheels are built here so the runtime image ships no compilers.
FROM python:3.13-slim AS build

WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- runtime -------------------------------------------------------------
FROM python:3.13-slim AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    WEB_CONCURRENCY=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" appuser

COPY --from=build /opt/venv /opt/venv
COPY --chown=appuser:appuser . .

RUN chmod +x /app/entrypoint.sh
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/ready" || exit 1

CMD ["/app/entrypoint.sh"]
