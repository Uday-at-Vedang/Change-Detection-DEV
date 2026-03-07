FROM python:3.11-slim

# System dependencies for OpenCV and image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (required by Hugging Face Spaces)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cache-bust: increment to force a fresh COPY on HF Spaces
ENV APP_BUILD=2

# Copy application code
COPY . .

# Create data directories with correct permissions
RUN mkdir -p data/overlays && chown -R appuser:appuser /app

USER appuser

# HF Spaces expects port 7860; Render expects 10000
# Use PORT env var with 7860 as default (works for both)
ENV PORT=7860
EXPOSE 7860

# Single process: uvicorn only (simpler for HF Spaces; avoid gunicorn restart issues)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
