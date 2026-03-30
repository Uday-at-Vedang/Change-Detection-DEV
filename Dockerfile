FROM python:3.11-slim

# Ensure build logs flush immediately (helps when HF shows “BUILDING” with no output)
ENV PYTHONUNBUFFERED=1

# Hugging Face Hub cache:
# Some Spaces build steps scan/download using the local Hugging Face cache.
# In containers this cache can be missing/unwritable unless we force it.
ENV HF_HOME=/tmp/hf
ENV HF_HUB_CACHE=/tmp/hf/hub
ENV TRANSFORMERS_CACHE=/tmp/hf/transformers

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

# Build-time info + cache-bust:
# Changing APP_BUILD forces Docker to re-run subsequent layers (including pip install).
ARG APP_BUILD=10
ENV APP_BUILD=${APP_BUILD}
RUN echo "Docker build start: APP_BUILD=${APP_BUILD}" && python -V

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check --default-timeout=120 -U pip setuptools wheel
RUN pip install --no-cache-dir --disable-pip-version-check --default-timeout=120 --prefer-binary -r requirements.txt -v

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
