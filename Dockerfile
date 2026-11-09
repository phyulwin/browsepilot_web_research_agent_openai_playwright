# Dockerfile — build the React client, then serve it alongside FastAPI in one container.
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# The Python runtime owns Chromium; end users need only a web browser.
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    DATA_DIR=/app/data \
    FRONTEND_DIR=/app/frontend/dist \
    PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && groupadd --gid 10001 browsepilot \
    && useradd --uid 10001 --gid browsepilot --create-home browsepilot \
    && mkdir -p /app/data \
    && chown -R browsepilot:browsepilot /app /opt/playwright \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=browsepilot:browsepilot backend/ ./backend/
USER browsepilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health',timeout=4)"
# One worker is required because the bounded job registry is intentionally in process.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]

# Cloud Run demo: ephemeral files, mounted secret configuration, and no frontend build.
FROM runtime AS cloudrun
ENV DATA_DIR=/tmp/browsepilot \
    FRONTEND_DIR=/app/no-frontend \
    BROWSEPILOT_ENV_FILE=/secrets/browsepilot/env \
    PORT=8080 \
    MAX_RUNTIME_SECONDS=1200 \
    MAX_CONCURRENT_SESSIONS=1 \
    CHROMIUM_SANDBOX=false \
    COOKIE_SECURE=true
EXPOSE 8080

# Preserve the existing default Docker/Compose single-origin deployment.
FROM runtime AS standalone
COPY --chown=browsepilot:browsepilot --from=frontend /build/dist/ ./frontend/dist/
