# Production image (SPEC §16.2): a Node stage builds the SPA, then a slim Python
# stage installs the server with uv (no dev deps) and serves the API plus the
# built dashboard through gunicorn + WhiteNoise, as a non-root user.

# syntax=docker/dockerfile:1

# --- Stage 1: build the React dashboard --------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Built with base=/static/ (see vite.config.ts) so assets resolve under WhiteNoise.
RUN npm run build

# --- Stage 2: Python runtime -------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=adjudik_server.settings.prod

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

WORKDIR /app
# Source for the uv workspace (root + members). Copied before sync so the
# editable workspace installs resolve.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY server/ server/
RUN uv sync --locked --no-dev

# The built SPA; collectstatic (at start) gathers it into server/staticfiles.
COPY --from=web /web/dist /app/web/dist

# Non-root runtime user (SPEC §15.7); owns /app so collectstatic can write.
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/server
EXPOSE 8000

# Default to the web server; workers/beat override the command in compose.
CMD ["gunicorn", "adjudik_server.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
