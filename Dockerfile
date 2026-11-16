FROM python:3.12-slim-bookworm

ARG VERSION=1.4.0
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="ClipMind" \
    org.opencontainers.image.description="Local video evidence: transcripts, screen text and timestamped frames" \
    org.opencontainers.image.source="https://github.com/yy31104/clipmind" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    io.modelcontextprotocol.server.name="io.github.yy31104/clipmind"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLIPMIND_OUT=/data \
    CLIPMIND_COOKIE_SOURCES=- \
    CLIPMIND_ASR_PROVIDER=faster-whisper \
    CLIPMIND_OCR_PROVIDER=tesseract \
    HF_HOME=/data/.cache/huggingface \
    UV_PROJECT_ENVIRONMENT=/opt/clipmind \
    UV_PYTHON=/usr/local/bin/python \
    PATH="/opt/clipmind/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg fonts-dejavu-core tesseract-ocr tesseract-ocr-eng \
       tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# An allowlisted build context keeps libraries, downloaded media, credentials
# and model weights out even when a developer has them beside the checkout.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY clipmind ./clipmind
RUN python -m pip install --no-cache-dir uv==0.11.14 \
    && uv sync --frozen --no-dev --no-editable \
    && uv cache clean

VOLUME ["/data"]
EXPOSE 8420
# 0.0.0.0 is the container's own interface; without it a published port cannot
# reach the server. The server has no authentication, so publish it on the
# host's loopback only: docker run -p 127.0.0.1:8420:8420 ...
CMD ["clipmind", "serve", "--host", "0.0.0.0", "--port", "8420"]
