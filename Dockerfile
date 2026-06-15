FROM python:3.11-slim

# Ensure build logs flush immediately (helps when HF shows “BUILDING” with no output)
ENV PYTHONUNBUFFERED=1

# System dependencies for OpenCV and image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (required by Hugging Face Spaces)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Build-time info + cache-bust:
# Changing APP_BUILD forces Docker to re-run subsequent layers (including pip install).
ARG APP_BUILD=30
ENV MAX_GEOTIFF_MB=5120
ENV APP_BUILD=${APP_BUILD}
ENV GDAL_CONFIG=/usr/bin/gdal-config
RUN echo "Docker build start: APP_BUILD=${APP_BUILD}" && python -V

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check --default-timeout=300 -U pip setuptools wheel
RUN pip install --no-cache-dir --disable-pip-version-check --default-timeout=300 --prefer-binary -r requirements.txt -v

# Pre-download the AdaptFormer model so cold starts are instant
ENV HF_HOME=/app/.hf_cache
RUN python -c "from transformers import AutoImageProcessor, AutoModel; \
    AutoImageProcessor.from_pretrained('deepang/adaptformer-LEVIR-CD', cache_dir='/app/.hf_cache', trust_remote_code=True); \
    AutoModel.from_pretrained('deepang/adaptformer-LEVIR-CD', cache_dir='/app/.hf_cache', trust_remote_code=True); \
    print('Model pre-downloaded successfully')"

# Copy application code
COPY . .

# Create data directories with correct permissions
RUN mkdir -p data/overlays && chown -R appuser:appuser /app

USER appuser

# HF Spaces generally uses 7860, but binding to $PORT is safer.
ENV PORT=7860
EXPOSE 7860

# Bind to runtime PORT so health checks always reach the server.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --log-level info --timeout-keep-alive 600"]
