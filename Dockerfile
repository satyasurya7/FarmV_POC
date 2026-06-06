FROM python:3.11-slim

WORKDIR /app

# System dependencies for asyncpg + audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# HTTP health (8080) + telephony WebSocket (8765)
EXPOSE 8080 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

CMD ["python", "-m", "src.server"]
