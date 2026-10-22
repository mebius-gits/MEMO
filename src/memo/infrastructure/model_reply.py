"""HTTP adapter for configurable Chat Completions-compatible endpoints."""

import json
import math
import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memo.domain.interaction import EmotionLabel, Reply
from memo.infrastructure.template_reply import TemplateReplyGenerator


class ModelReplyGenerator:
    def __init__(self, settings, opener=urlopen):
        self.settings = settings
        self.opener = opener
        self.fallback = TemplateReplyGenerator()

    def generate(self, prepared):
        settings = self.settings
        if not settings.llm_base_url or not settings.llm_model:
            return self.fallback.generate(prepared)
        payload = {
            "model": settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": prepared.system_prompt
                    + '\nJSON contract: {"text":"繁體中文，1至2000字",'
                    '"emotion":{"label":"neutral|friendly|concerned|happy|confused",'
                    '"intensity":0.4}}. No other fields.',
                },
                {"role": "user", "content": prepared.user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + settings.llm_api_key.get_secret_value()
        request = Request(
            str(settings.llm_base_url).rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=settings.llm_timeout_seconds) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError("oversized response")
            result = json.loads(json.loads(raw)["choices"][0]["message"]["content"])
            if set(result) != {"text", "emotion"} or set(result["emotion"]) != {
                "label",
                "intensity",
            }:
                raise ValueError("unexpected fields")
            text = result["text"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                raise ValueError("invalid text")
            emotion = EmotionLabel(result["emotion"]["label"])
            intensity = result["emotion"]["intensity"]
            if (
                type(intensity) not in (int, float)
                or not math.isfinite(intensity)
                or not 0 <= intensity <= 1
            ):
                raise ValueError("invalid intensity")
            # A rule-driven notification's expression cannot contradict its lifecycle.
            if prepared.input.proactive and prepared.input.event:
                emotion = (
                    EmotionLabel.HAPPY
                    if prepared.input.event.status == "resolved"
                    else EmotionLabel.CONCERNED
                )
            return Reply(text.strip(), emotion, intensity, "model", None)
        except (TimeoutError, socket.timeout):
            reason = "model_timeout"
        except (HTTPError, URLError, OSError):
            reason = "model_unavailable"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            reason = "invalid_model_output"
        return replace(self.fallback.generate(prepared), fallback_reason=reason)
