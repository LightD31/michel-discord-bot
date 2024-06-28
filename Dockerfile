# Use a specific Python image
FROM python:3.12.3

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

# Start the bot
CMD ["python", "main.py"]
