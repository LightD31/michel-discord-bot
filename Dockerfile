# syntax=docker/dockerfile:1.7

# Every base is pinned by digest so builds are reproducible; Dependabot (docker
# ecosystem) bumps the tag and the digest together. Keep the uv version in step
# with UV_TOOL_VERSION in .github/workflows/ci.yml.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8 AS builder

# Bytecode is compiled at install time (as pip did) for a faster first start;
# copy mode because the uv cache mount sits on another filesystem; the image's
# own interpreter is used, never a downloaded one.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

# Install from the committed lockfile (uv.lock, see CLAUDE.md) so image builds
# are reproducible; --locked fails the build if it lags behind pyproject.toml.
# Copy only the two files uv needs first, so the dep-install layer stays cached
# as long as the pins don't change. Dev tools are left out.
COPY pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ─── runtime ───────────────────────────────────────────────────────────
FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8 AS runtime

LABEL org.opencontainers.image.source="https://github.com/LightD31/michel-discord-bot" \
      org.opencontainers.image.description="Michel — modular multi-guild Discord bot (interactions.py + MongoDB + optional FastAPI dashboard)." \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --system --create-home --uid 1000 michel

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=michel:michel ./ /app/

USER michel

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, time; s=os.stat('/tmp/bot_heartbeat'); exit(0 if time.time()-s.st_mtime < 60 else 1)"

CMD ["python", "main.py"]
