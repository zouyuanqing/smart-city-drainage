FROM nvidia/cuda:12.1-runtime-ubuntu22.04

LABEL maintainer="Smart City Neural Endpoints Team"
LABEL description="FastAPI backend with YOLO inference support"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3.11-venv python3-pip \
    ffmpeg libsm6 libxext6 libgl1 libglib2.0-0 \
    libavcodec-dev libavformat-dev libavdevice-dev \
    libswscale-dev libavutil-dev \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 scn \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin scn

# Create virtual environment
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies from the hash-locked GPU profile
COPY requirements-py311-gpu.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements-py311-gpu.lock \
    && pip check

# Copy application
COPY --chown=scn:scn . .

# Create writable runtime directories before dropping root. The
# /var/lib/smart-drainage roots are the workers' attempt sandbox, published
# result, and network-job staging volumes; Compose backs them with fresh named
# volumes, and a named volume whose mount point is absent from the image is
# created root-owned, which would fail a worker's fail-closed
# `workspace_not_writable` / `staging_root_not_writable` startup check under
# `user: "10001:10001"`.
RUN mkdir -p /app/logs /app/media/hls /app/media/screenshots /app/models/weights \
        /var/lib/smart-drainage/sandbox /var/lib/smart-drainage/results \
        /var/lib/smart-drainage/import \
    && chown -R scn:scn /app/logs /app/media /app/models \
        /var/lib/smart-drainage

USER 10001:10001

# Image-level liveness only; Compose supplies the dependency-aware readiness
# check for services that participate in the deployment topology.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
