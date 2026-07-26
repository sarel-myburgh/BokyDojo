# =============================================================================
# DojoMaster — Multi-stage Dockerfile
# TODO 0.2.1 — build deps separated, non-root runtime user
# =============================================================================

# -- Stage 1: build dependencies -----------------------------------------------
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .


# -- Stage 2: runtime -----------------------------------------------------------
FROM python:3.13-slim AS runtime

# Runtime deps: only libpq for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Copy pre-built Python packages from builder
COPY --from=builder /install /usr/local

# Non-root user
RUN groupadd -r dojomaster && useradd -r -g dojomaster -d /app -s /sbin/nologin dojomaster

WORKDIR /app

COPY . .

RUN chown -R dojomaster:dojomaster /app

USER dojomaster

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
