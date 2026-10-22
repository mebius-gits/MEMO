FROM python:3.11-slim

# Models are fetched at build time so the container starts offline and fast.
RUN apt-get update && apt-get install -y --no-install-recommends curl bzip2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /models
ARG SENSE_VOICE=https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main
RUN mkdir sense-voice \
    && curl -fsSL -o sense-voice/model.int8.onnx "$SENSE_VOICE/model.int8.onnx" \
    && curl -fsSL -o sense-voice/tokens.txt "$SENSE_VOICE/tokens.txt"
RUN curl -fsSL https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2 \
    | tar -xj && mv kokoro-multi-lang-v1_1 kokoro

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py smoke_test.py ./

ENV SPEECH_THREADS=2 TTS_SPEAKER_ID=3 TTS_SPEED=1.3
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
