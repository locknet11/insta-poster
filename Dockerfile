# Stage 1: Build React frontend
FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + built static SPA
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    INSTA_POSTER_HOST=0.0.0.0 \
    INSTA_POSTER_PORT=8000 \
    INSTA_POSTER_DATA_DIR=/app/data

COPY pyproject.toml uv.lock README.md ./
COPY backend/ ./backend/

RUN uv sync --frozen --no-dev

# Copy compiled React SPA into backend/insta_poster/static
COPY --from=frontend-builder /build/backend/insta_poster/static ./backend/insta_poster/static

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["insta-poster", "serve"]
