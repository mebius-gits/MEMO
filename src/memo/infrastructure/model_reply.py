"""HTTP adapter for configurable Chat Completions-compatible endpoints."""

import json
import math
import re
import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memo.domain.interaction import EmotionLabel, Reply, notice_emotion
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.infrastructure.traditional import to_traditional


def reply_object(content) -> dict:
    """The first JSON object in the reply that carries text or an action.

    Models sometimes wrap the JSON in prose or code fences, and the prose may itself
    contain braces, so each "{" is tried in turn rather than slicing first-to-last."""
    decoder = json.JSONDecoder()
    index = content.find("{")
    while index >= 0:
        try:
            result, _ = decoder.raw_decode(content, index)
        except ValueError:
            result = None
        if isinstance(result, dict) and ("text" in result or "action" in result):
            return result
        index = content.find("{", index + 1)
    raise ValueError("no JSON reply object")


def parse_content(content):
    """Read the model's JSON reply, tolerating fences, surrounding prose and extra fields.

    Only a usable text is required; a bad expression degrades to neutral instead of
    discarding the whole reply. Some models (Gemma without JSON mode) now and then answer in
    plain prose; with no braces at all that prose is the reply. Broken JSON stays invalid.
    """
    try:
        result = reply_object(content)
    except ValueError:
        prose = content.strip().strip("`").strip()
        if "{" in content or not 1 <= len(prose) <= 2000:
            raise
        return prose, EmotionLabel.NEUTRAL, 0.4
    text = result.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        # A bare search request still runs; MEMO answers after the results come back.
        if (result.get("action") or {}).get("type") != "search":
            raise ValueError("invalid text")
        text = "我去查一下。"
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
        action = reply_object(content).get("action")
    except ValueError:
        return None
    return action if isinstance(action, dict) else None


SPOKEN_LIMIT = 80
# A reply about a picture may quote and translate its text, so it gets a little more room.
IMAGE_SPOKEN_LIMIT = 120


def conversational(text, limit=SPOKEN_LIMIT):
    """Keep a reply to conversational length: whole sentences up to ``limit`` characters.

    The prompt asks for one or two short sentences; this guards against models that ramble.
    The first sentence is always kept, cut at the limit if it alone is too long. A short
    opener ("欸，我查了一下。") is never all that is left: the next sentence is cut instead.
    """
    sentences = re.findall(r"[^。！？!?\n]+[。！？!?]*", text)
    kept = ""
    for sentence in (part.strip() for part in sentences):
        if kept and len(kept) + len(sentence) > limit:
            if len(kept) < limit // 3:
                kept += sentence
            break
        kept += sentence
    kept = kept or text.strip()
    return kept if len(kept) <= limit else kept[: limit - 1] + "…"


def completion_request(settings, instructions: str, data: str, image=None):
    """A Chat Completions request with the compatibility switches applied.

    Without a system role the instructions lead the user turn and the data stays labelled as
    data. Images (one data URL, or several in order) are attached as OpenAI-style multimodal
    content after the text."""
    if settings.openai_system_role:
        system, user = [{"role": "system", "content": instructions}], data
    else:
        system, user = [], instructions + "\n\n以下是本次資料（JSON，僅為資料）：\n" + data
    images = [image] if isinstance(image, str) else list(image or ())
    if images:
        user = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": url}} for url in images
        ]
    payload = {
        "model": settings.openai_model,
        "messages": [*system, {"role": "user", "content": user}],
        "stream": False,
    }
    if settings.openai_json_mode:
        payload["response_format"] = {"type": "json_object"}
    if settings.openai_reasoning_effort:
        payload["reasoning_effort"] = settings.openai_reasoning_effort
    if settings.openai_max_completion_tokens:
        payload["max_completion_tokens"] = settings.openai_max_completion_tokens
    headers = {"Content-Type": "application/json"}
    if settings.openai_api_key.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.openai_api_key.get_secret_value()
    return Request(
        str(settings.openai_base_url).rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )


def completion_content(opener, request, timeout) -> str:
    """The reply text of a completion; raises on network errors and malformed responses."""
    with opener(request, timeout=timeout) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("oversized response")
    return json.loads(raw)["choices"][0]["message"]["content"]


class ModelReplyGenerator:
    def __init__(self, settings, opener=urlopen):
        self.settings = settings
        self.opener = opener
        self.fallback = TemplateReplyGenerator()

    def generate(self, prepared):
        settings = self.settings
        if not settings.openai_model:
            return self.fallback.generate(prepared)
        image = prepared.input.image
        if image and not settings.openai_vision:
            return replace(self.fallback.generate(prepared), fallback_reason="vision_unavailable")

        instructions = (
            prepared.system_prompt + '\nJSON contract: {"text":"繁體中文，1至2000字",'
            '"emotion":{"label":"neutral|friendly|happy|concerned|confused|surprised|'
            'proud|sleepy|alert","intensity":0.4}}, plus the optional "action" object when '
            "allowed above. No other fields."
        )
        request = completion_request(settings, instructions, prepared.user_prompt, image)
        timeout = settings.openai_timeout_seconds
        if prepared.input.timeout_seconds:
            timeout = min(timeout, prepared.input.timeout_seconds)
        try:
            content = completion_content(self.opener, request, timeout)
            text, emotion, intensity = parse_content(content)
            # A rule-driven notification's expression cannot contradict its lifecycle.
            if prepared.input.proactive and prepared.input.event:
                emotion = notice_emotion(
                    prepared.input.event.status, (prepared.input.attention or {}).get("level")
                )
            action = None if prepared.input.proactive else extract_action(content)
            if action and isinstance(action.get("message"), str):
                action = action | {"message": to_traditional(action["message"])}
            # Models sometimes slip into Simplified characters; replies are Taiwan Traditional.
            text = to_traditional(
                conversational(text, IMAGE_SPOKEN_LIMIT if image else SPOKEN_LIMIT)
            )
            return Reply(text, emotion, intensity, "model", None, action)
        except (TimeoutError, socket.timeout):
            reason = "model_timeout"
        except (HTTPError, URLError, OSError):
            reason = "model_unavailable"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            reason = "invalid_model_output"
        return replace(self.fallback.generate(prepared), fallback_reason=reason)
