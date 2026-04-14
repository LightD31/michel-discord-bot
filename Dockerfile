# Use a specific Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Set the working directory
WORKDIR /app

# Copy and install requirements separately to leverage caching
COPY ./requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install --upgrade -r /app/requirements.txt

# Copy the rest of the application code
COPY ./ /app/

# Healthcheck: verify the bot heartbeat file was written within the last 60 seconds
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, time; s=os.stat('/tmp/bot_heartbeat'); exit(0 if time.time()-s.st_mtime < 60 else 1)"

# Start the bot
CMD ["python", "main.py"]
