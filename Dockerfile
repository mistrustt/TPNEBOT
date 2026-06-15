# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

# System deps:
#   - build-essential / gcc / libffi-dev: build cryptography, asyncpg, greenlet wheels for some arches
#   - libpq-dev: asyncpg + psycopg-style libs prefer building against libpq headers
#   - libxml2-dev / libxslt1-dev / libjpeg-dev / libpng-dev / libfreetype6-dev:
#       matplotlib / Pillow / moviepy transitive deps
#   - ffmpeg: discord_games image/video generation
#   - curl: HEALTHCHECK + convenience
#   - tini: proper PID 1 signal handling for graceful discord.py shutdown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        libffi-dev \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        libjpeg-dev \
        libpng-dev \
        libfreetype6-dev \
        zlib1g-dev \
        ffmpeg \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Dedicated non-root user
RUN groupadd --system --gid 1000 tpnebot \
    && useradd  --system --uid 1000 --gid tpnebot --create-home --shell /usr/sbin/nologin tpnebot

WORKDIR /app

# Install Python deps first for layer caching
COPY --chown=tpnebot:tpnebot requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy source
COPY --chown=tpnebot:tpnebot . .

# discord.log + cache live here; keep it writable for the non-root user
RUN mkdir -p /app/data \
    && chown -R tpnebot:tpnebot /app

USER tpnebot

# AdminAPIServer (aiohttp) listens here by default; expose when running compose
EXPOSE 8080

# Liveness: admin API exposes /status on the same port
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${ADMIN_API_PORT:-8080}/status" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "bot.py"]
