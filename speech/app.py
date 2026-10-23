"""Local speech service: SenseVoice-Small STT and Kokoro-82M v1.1-zh TTS on CPU via sherpa-onnx."""

import io
import json
import os
import re
import threading
import wave

import numpy as np
import sherpa_onnx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from opencc import OpenCC
from pydantic import BaseModel, Field

THREADS = int(os.getenv("SPEECH_THREADS", "2"))
# Kokoro v1.1-zh speaker ids: 0-2 are English voices, Chinese voices start at 3 (zf_001).
SPEAKER_ID = int(os.getenv("TTS_SPEAKER_ID", "3"))
# Kokoro's 1.0 is about 4 chars/s; 1.3 gives ~5, the pace of everyday Mandarin conversation.
DEFAULT_SPEED = float(os.getenv("TTS_SPEED", "1.3"))
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 30
SENSE_VOICE, KOKORO = "/models/sense-voice", "/models/kokoro"

recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=f"{SENSE_VOICE}/model.int8.onnx",
    tokens=f"{SENSE_VOICE}/tokens.txt",
    num_threads=THREADS,
    use_itn=True,
    language="auto",
)
tts = sherpa_onnx.OfflineTts(
    sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=f"{KOKORO}/model.onnx",
                voices=f"{KOKORO}/voices.bin",
                tokens=f"{KOKORO}/tokens.txt",
                data_dir=f"{KOKORO}/espeak-ng-data",
                dict_dir=f"{KOKORO}/dict",
                lexicon=f"{KOKORO}/lexicon-us-en.txt,{KOKORO}/lexicon-zh.txt",
            ),
            num_threads=THREADS,
        ),
        rule_fsts=f"{KOKORO}/phone-zh.fst,{KOKORO}/date-zh.fst,{KOKORO}/number-zh.fst",
        max_num_sentences=1,
    )
)
# SenseVoice writes Simplified Chinese; Kokoro's lexicon only knows Simplified characters.
to_traditional, to_simplified = OpenCC("s2twp"), OpenCC("tw2sp")
# Each ONNX runtime is shared; serialise its calls rather than rely on undocumented thread
# safety. Separate locks, so recognising the user never queues behind a long synthesis.
stt_lock, tts_lock = threading.Lock(), threading.Lock()

app = FastAPI(title="MEMO speech", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=json.loads(os.getenv("CORS_ORIGINS", "[]")),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def read_wav(data: bytes) -> tuple[int, np.ndarray]:
    """Decode 16-bit PCM WAV (any rate, mono or stereo) into mono float32 samples."""
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if wav.getsampwidth() != 2:
                raise HTTPException(415, "需要 16-bit PCM WAV。")
            rate, channels = wav.getframerate(), wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as error:
        raise HTTPException(415, "需要 16-bit PCM WAV。") from error
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if not len(samples):
        raise HTTPException(422, "音訊是空的。")
    if len(samples) > rate * MAX_AUDIO_SECONDS:
        raise HTTPException(413, f"音訊最長 {MAX_AUDIO_SECONDS} 秒。")
    return rate, samples


def write_wav(samples, rate: int) -> bytes:
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def trim_silence(samples, rate: int, head: float = 0.05, tail: float = 0.12):
    """Drop leading/trailing silence so sentence-by-sentence playback flows like speech."""
    samples = np.asarray(samples, dtype=np.float32)
    voiced = np.flatnonzero(np.abs(samples) > 0.01)
    if not len(voiced):
        return samples
    start = max(0, voiced[0] - int(rate * head))
    end = min(len(samples), voiced[-1] + int(rate * tail))
    return samples[start:end]


def speakable(text: str) -> str:
    """Spell out units and symbols the Chinese front end would otherwise skip or misread."""
    text = re.sub(r"CO\s*[₂2]", "二氧化碳", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*°\s*C", r"\1度", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[%％]", r"百分之\1", text)
    text = re.sub(r"\s*ppm\b", " PPM", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*lux\b", "勒克斯", text, flags=re.IGNORECASE)
    # Emoji and other symbols carry no speech.
    text = re.sub(r"[^\w\s，。！？、；：,.!?;:\"'「」『』（）()~～\-]", "", text)
    return to_simplified.convert(text).strip()


@app.get("/health")
def health():
    return {
        "status": "ready",
        "speakers": tts.num_speakers,
        "speaker_id": SPEAKER_ID,
        "speed": DEFAULT_SPEED,
    }


@app.post("/stt")
async def speech_to_text(request: Request):
    data = await request.body()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "音訊檔太大。")
    rate, samples = read_wav(data)
    with stt_lock:
        stream = recognizer.create_stream()
        stream.accept_waveform(rate, samples)
        recognizer.decode_stream(stream)
        result = stream.result
    tag = lambda value: (value or "").strip("<|>").lower() or None  # noqa: E731
    return {
        # Everyday Taiwanese usage prefers 濕 over the dictionary variant 溼.
        "text": to_traditional.convert(result.text.strip()).replace("溼", "濕"),
        "language": tag(result.lang),
        "emotion": tag(result.emotion),
        "event": tag(result.event),
    }


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    speed: float | None = Field(default=None, ge=0.5, le=2.0, description="省略時用 TTS_SPEED。")


@app.post("/tts")
def text_to_speech(payload: SpeechRequest):
    text = speakable(payload.text)
    if not text:
        raise HTTPException(422, "沒有可以朗讀的文字。")
    with tts_lock:
        audio = tts.generate(text, sid=SPEAKER_ID, speed=payload.speed or DEFAULT_SPEED)
    if not len(audio.samples):
        raise HTTPException(500, "語音合成失敗。")
    samples = trim_silence(audio.samples, audio.sample_rate)
    return Response(write_wav(samples, audio.sample_rate), media_type="audio/wav")
