"""Round trip against the running service: text -> /tts -> /stt -> text.

Run inside the container: docker compose exec speech python smoke_test.py
"""

import json
import re
import time
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
SENTENCE = "空氣有點悶，要不要起來走走？"

started = time.perf_counter()
wav = urlopen(
    Request(
        f"{BASE}/tts", json.dumps({"text": SENTENCE}).encode(), {"Content-Type": "application/json"}
    ),
    timeout=60,
).read()
synthesised = time.perf_counter()
heard = json.load(urlopen(Request(f"{BASE}/stt", wav, {"Content-Type": "audio/wav"}), timeout=60))
recognised = time.perf_counter() - synthesised
print(f"TTS {synthesised - started:.2f}s, STT {recognised:.2f}s: {heard['text']}")
# Punctuation is the recogniser's guess from intonation; compare the words only.
words = lambda text: re.sub(r"[^\w]", "", text)  # noqa: E731
assert words(heard["text"]) == words(SENTENCE), heard
print("PASS")
