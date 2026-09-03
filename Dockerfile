FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLIPMIND_OUT=/data \
    CLIPMIND_ASR_PROVIDER=faster-whisper \
    CLIPMIND_OCR_PROVIDER=tesseract

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8420
CMD ["clipmind", "serve", "--host", "0.0.0.0", "--port", "8420"]
