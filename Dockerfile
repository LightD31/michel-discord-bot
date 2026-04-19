# Use a specific Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Set the working directory
WORKDIR /app

# Install dependencies from pyproject.toml. Copy only the metadata files so
# this layer stays cached as long as the project metadata doesn't change.
COPY pyproject.toml README.md LICENSE /app/
RUN pip install --upgrade pip \
    && pip install .

# Copy the rest of the application code (runtime modules live under /app and
# are picked up via PYTHONPATH — they are not installed into site-packages).
COPY ./ /app/

# Healthcheck: verify the bot heartbeat file was written within the last 60 seconds
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, time; s=os.stat('/tmp/bot_heartbeat'); exit(0 if time.time()-s.st_mtime < 60 else 1)"

# Start the bot
CMD ["python", "main.py"]
