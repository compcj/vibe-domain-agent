# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS dependencies
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.6
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

FROM python:3.12-slim-bookworm AS application
ARG VCS_REF=development
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="Vibe Domain Agent" \
      org.opencontainers.image.source="https://github.com/compcj/vibe-domain-agent" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    FRONTEND_DIST=/app/frontend/dist STORAGE_DIR=/app/data/files
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app/data/files && chown -R app:app /app
COPY --from=dependencies /app/.venv /app/.venv
COPY --chown=app:app backend/ backend/
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts/ scripts/
COPY --chown=app:app deploy/entrypoint.sh deploy/entrypoint.sh
COPY --from=web --chown=app:app /web/dist frontend/dist/
RUN chmod +x deploy/entrypoint.sh
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" || exit 1
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["api"]
