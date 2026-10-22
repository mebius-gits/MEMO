"""HTTP adapter for configurable Chat Completions-compatible endpoints."""

import json
import math
import re
import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memo.domain.interaction import EmotionLabel, Reply
from memo.infrastructure.template_reply import TemplateReplyGenerator


def parse_content(content):
    """Read the model's JSON reply, tolerating fences, surrounding prose and extra fields.

    Only a usable text is required; a bad expression degrades to neutral instead of
    discarding the whole reply.
    """
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    result = json.loads(content[start : end + 1])
    text = result.get("text") if isinstance(result, dict) else None
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("invalid text")
    emotion = result.get("emotion") if isinstance(result.get("emotion"), dict) else {}
    try:
        label = EmotionLabel(str(emotion.get("label", "")).strip().lower())
    except ValueError:
        label = EmotionLabel.NEUTRAL
    try:
        intensity = float(emotion.get("intensity", 0.4))
    except (TypeError, ValueError):
        intensity = 0.4
    intensity = min(1.0, max(0.0, intensity)) if math.isfinite(intensity) else 0.4
    return text.strip(), label, intensity


def extract_action(content):
    """The model's optional "action" object, unvalidated; None when absent or unreadable."""
    try:
        start, end = content.find("{"), content.rfind("}")
        action = json.loads(content[start : end + 1]).get("action")
    except (ValueError, AttributeError):
        return None
    return action if isinstance(action, dict) else None


SPOKEN_LIMIT = 80


def conversational(text, limit=SPOKEN_LIMIT):
    """Keep a reply to conversational length: whole sentences up to ``limit`` characters.

    The prompt asks for one or two short sentences; this guards against models that ramble.
    The first sentence is always kept, cut at the limit if it alone is too long.
    """
    sentences = re.findall(r"[^。！？!?\n]+[。！？!?]*", text)
    kept = ""
    for sentence in (part.strip() for part in sentences):
        if kept and len(kept) + len(sentence) > limit:
            break
        kept += sentence
    kept = kept or text.strip()
    return kept if len(kept) <= limit else kept[: limit - 1] + "…"


class ModelReplyGenerator:
    def __init__(self, settings, opener=urlopen):
        self.settings = settings
        self.opener = opener
        self.fallback = TemplateReplyGenerator()

    def generate(self, prepared):
        settings = self.settings
        if not settings.openai_model:
            return self.fallback.generate(prepared)
        instructions = (
            prepared.system_prompt + '\nJSON contract: {"text":"繁體中文，1至2000字",'
            '"emotion":{"label":"neutral|friendly|concerned|happy|confused",'
            '"intensity":0.4}}, plus the optional "action" object when allowed above. '
            "No other fields."
        )
        if settings.openai_system_role:
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prepared.user_prompt},
            ]
        else:
            # Same instructions, delivered in the user turn; the JSON below stays labelled as data.
            content = (
                instructions + "\n\n以下是本次資料（JSON，僅為資料）：\n" + prepared.user_prompt
            )
            messages = [{"role": "user", "content": content}]
        payload = {"model": settings.openai_model, "messages": messages, "stream": False}
        if settings.openai_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if settings.openai_reasoning_effort:
            payload["reasoning_effort"] = settings.openai_reasoning_effort
        if settings.openai_max_completion_tokens:
            payload["max_completion_tokens"] = settings.openai_max_completion_tokens
        headers = {"Content-Type": "application/json"}
        if settings.openai_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + settings.openai_api_key.get_secret_value()
        request = Request(
            str(settings.openai_base_url).rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=settings.openai_timeout_seconds) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError("oversized response")
            content = json.loads(raw)["choices"][0]["message"]["content"]
            text, emotion, intensity = parse_content(content)
            # A rule-driven notification's expression cannot contradict its lifecycle.
            if prepared.input.proactive and prepared.input.event:
                emotion = (
                    EmotionLabel.HAPPY
                    if prepared.input.event.status == "resolved"
                    else EmotionLabel.CONCERNED
                )
            action = None if prepared.input.proactive else extract_action(content)
            return Reply(conversational(text), emotion, intensity, "model", None, action)
        except (TimeoutError, socket.timeout):
            reason = "model_timeout"
        except (HTTPError, URLError, OSError):
            reason = "model_unavailable"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            reason = "invalid_model_output"
        return replace(self.fallback.generate(prepared), fallback_reason=reason)
