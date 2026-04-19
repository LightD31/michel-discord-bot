# syntax=docker/dockerfile:1.7

# Pinned by digest so the base is reproducible; Dependabot (docker ecosystem)
# bumps both the tag and the digest together.
FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only the metadata first so the dep-install layer stays cached as long
# as pyproject.toml/README/LICENSE don't change.
COPY pyproject.toml README.md LICENSE /app/
RUN pip install --upgrade pip && pip install .

# ─── runtime ───────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286 AS runtime

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
