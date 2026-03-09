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
ENV APP_BUILD=3

# Copy application code
COPY . .

# Create data directories with correct permissions
RUN mkdir -p data/overlays && chown -R appuser:appuser /app

USER appuser

# HF Spaces expects port 7860.
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
EXPOSE 7860

# Use direct exec form so container startup is simpler and logs flush reliably.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--log-level", "info"]
