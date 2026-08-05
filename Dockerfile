# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: builder
# Installs dependencies into an isolated venv so the final image doesn't
# carry build tools, caches, or compilers.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps only where required (kept minimal on purpose)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime
# Minimal slim image, non-root user, only the venv + app source copied in.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="production-grade-cicd" \
      org.opencontainers.image.description="Sample FastAPI service for CI/CD to AWS ECS" \
      org.opencontainers.image.source="https://github.com/krnpatel88/production-grade-cicd"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Create a dedicated non-root user/group for running the app
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --shell /usr/sbin/nologin --no-create-home appuser

# curl is needed only for the container HEALTHCHECK below.
# Not pinned to an exact patch version here because it drifts with the
# python:3.12-slim base image; Trivy (in CI) still catches vulnerable curl
# builds regardless of the version installed.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# Ensure the non-root user owns application files
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:${PORT}/health || exit 1

ENTRYPOINT ["uvicorn"]
CMD ["app.main:app", "--host", "0.0.0.0", "--port", "8000"]
