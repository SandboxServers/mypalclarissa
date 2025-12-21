# Backend Dockerfile for MyPalClara API
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Configure Poetry to not create virtual environment
RUN poetry config virtualenvs.create false

# Install dependencies (production only)
RUN poetry install --only main --no-interaction --no-ansi

# Copy application code
COPY *.py ./

# Copy user_profile.txt if it exists (optional)
RUN touch /app/user_profile.txt
COPY user_profile.tx[t] ./

# Create directory for persistent data (mounted as volume)
RUN mkdir -p /data
ENV DATA_DIR=/data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the API
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
