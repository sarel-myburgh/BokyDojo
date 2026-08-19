# =============================================================================
# BokyDojo — Multi-stage image
# TODO 0.2.1 — build deps separated, non-root runtime user
#
# Built and published by .github/workflows/publish.yml to
#   ghcr.io/sarel-myburgh/bokydojo
# for linux/amd64 and linux/arm64, so it runs under Docker or Podman on an
# ordinary server and on an Apple-silicon laptop without rebuilding.
# =============================================================================

# -- Stage 1: build dependencies -----------------------------------------------
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Only the manifest, so a code change does not invalidate the dependency layer.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .


# -- Stage 2: runtime -----------------------------------------------------------
FROM python:3.13-slim AS runtime

# ⚠ OCI labels, not decoration: ghcr.io links the package to this repository
# through org.opencontainers.image.source, which is also what makes the package
# inherit the repository's visibility settings.
LABEL org.opencontainers.image.source="https://github.com/sarel-myburgh/BokyDojo" \
      org.opencontainers.image.description="Student information system for martial arts organisations" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

# Runtime deps: only libpq for psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN groupadd -r bokydojo && useradd -r -g bokydojo -d /app -s /sbin/nologin bokydojo

WORKDIR /app

COPY . .

# ⚠ Created and owned *in the image*, before any volume is mounted over them.
# A named volume inherits the ownership of the directory it covers, so without
# this the non-root user cannot write and `collectstatic` fails on first boot
# with a permission error that reads like a bug in Django.
RUN mkdir -p /app/staticfiles /app/media && \
    chown -R bokydojo:bokydojo /app && \
    chmod +x /app/docker/entrypoint.sh

USER bokydojo

EXPOSE 8000

# ⚠ Baked in at build time, and read by apps/core/version.py for the badge in
# the corner. Without it a container has no way to say which commit it is, and
# "I pulled and nothing changed" cannot be diagnosed from the screen. Last
# layer-ish on purpose: it changes every build and would bust the cache above.
ARG REVISION=dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BOKYDOJO_REVISION=${REVISION} \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# ⚠ Python, not curl: the slim runtime has no curl and adding one for a
# healthcheck is a package and a CVE surface for no reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
