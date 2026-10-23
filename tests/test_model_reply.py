import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from memo.application.interaction import InteractionService
from memo.domain.interaction import InteractionInput, Personality
from memo.infrastructure.model_reply import ModelReplyGenerator, conversational, parse_content
from memo.infrastructure.settings import Settings
from memo.infrastructure.traditional import to_traditional


def test_model_request_and_validated_output():
    captured = {}

    def opener(request, timeout):
        captured.update(
            url=request.full_url,
            payload=json.loads(request.data),
            timeout=timeout,
            authorization=request.get_header("Authorization"),
        )
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "text": "我在這裡陪你。",
                                        "emotion": {"label": "friendly", "intensity": 0.4},
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()
        )

    settings = Settings(
        openai_base_url="http://model.local/v1",
        openai_model="test-model",
        openai_api_key="fake-test-key",
        _env_file=None,
    )
    service = InteractionService(ModelReplyGenerator(settings, opener))
    prepared = service.prepare(
        InteractionInput(
            "你好",
            Personality(),
            history=({"role": "user", "content": "我叫小明"},),
            task_memory=({"id": "task-1", "status": "acknowledged"},),
        ),
        datetime.now(timezone.utc),
    )
    reply = service.respond(prepared)
    assert reply.source == "model" and reply.fallback_reason is None
    assert captured["url"] == "http://model.local/v1/chat/completions"
    assert captured["authorization"] == "Bearer fake-test-key"
    # Optional OpenAI parameters are omitted unless configured.
    assert "reasoning_effort" not in captured["payload"]
    assert "max_completion_tokens" not in captured["payload"]
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    content = json.loads(captured["payload"]["messages"][1]["content"])
    assert content["task_memory"][0]["status"] == "acknowledged"
    assert content["history"][0]["content"] == "我叫小明"
    assert "fake-test-key" not in repr(settings)


@pytest.mark.parametrize(
    "failure,reason",
    [
        (TimeoutError(), "model_timeout"),
        (HTTPError("http://model", 503, "unavailable", {}, None), "model_unavailable"),
        (b'{"choices":[{"message":{"content":"not-json"}}]}', "invalid_model_output"),
        (b"x" * 131073, "invalid_model_output"),
    ],
    ids=["timeout", "unavailable", "invalid-json", "oversized"],
)
def test_model_failures_are_explicit_fallback(failure, reason):
    def opener(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return io.BytesIO(failure)

    generator = ModelReplyGenerator(
        Settings(openai_base_url="http://model/v1", openai_model="test", _env_file=None), opener
    )
    service = InteractionService(generator)
    result = service.respond(
        service.prepare(InteractionInput("Hi", Personality()), datetime.now(timezone.utc))
    )
    assert result.source == "template" and result.fallback_reason == reason


@pytest.mark.parametrize(
    "content,expected",
    [
        (
            '```json\n{"text": "嗨", "emotion": {"label": "Happy", "intensity": "0.7"}}\n```',
            ("嗨", "happy", 0.7),
        ),
        (
            '好的：{"text": "嗨", "emotion": {"label": "excited", "intensity": 3}, "extra": 1}',
            ("嗨", "neutral", 1.0),
        ),
        ('{"text": " 嗨 "}', ("嗨", "neutral", 0.4)),
    ],
    ids=["fenced-string-intensity", "prose-unknown-label-extra-field", "missing-emotion"],
)
def test_lenient_model_output(content, expected):
    text, label, intensity = parse_content(content)
    assert (text, label.value, intensity) == expected


@pytest.mark.parametrize(
    "content", ['{"emotion": {"label": "happy"}}', '{"text": ""}', "沒有 JSON"]
)
def test_model_output_without_text_is_rejected(content):
    with pytest.raises(ValueError):
        parse_content(content)


def test_replies_are_kept_to_conversational_length():
    short = "空氣有點悶。要不要起來走走？"
    assert conversational(short) == short
    rambling = "現在溫度 35 度，有點熱。" * 10
    kept = conversational(rambling)
    assert kept.endswith("。") and len(kept) <= 80 and kept.startswith("現在溫度 35 度")
    run_on = "這" * 120
    assert conversational(run_on) == "這" * 79 + "…"
    # A filler opener must not swallow the answer that follows it.
    answer = "欸，我查了一下。" + "沃爾瑪今天發表新工具，讓 AI 代理代替顧客購物" * 3 + "。"
    kept = conversational(answer)
    assert "沃爾瑪" in kept and len(kept) == 80 and kept.endswith("…")


def test_optional_openai_parameters_are_sent_when_configured():
    captured = {}

    def opener(request, timeout):
        captured.update(json.loads(request.data))
        content = json.dumps({"text": "嗨", "emotion": {"label": "happy", "intensity": 0.5}})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    settings = Settings(
        openai_model="gpt-oss-120B",
        openai_reasoning_effort="low",
        openai_max_completion_tokens=400,
        _env_file=None,
    )
    service = InteractionService(ModelReplyGenerator(settings, opener))
    reply = service.respond(
        service.prepare(InteractionInput("嗨", Personality()), datetime.now(timezone.utc))
    )
    assert reply.source == "model"
    assert captured["reasoning_effort"] == "low"
    assert captured["max_completion_tokens"] == 400


def test_compatibility_switches_merge_instructions_and_drop_json_mode():
    captured = {}

    def opener(request, timeout):
        captured.update(json.loads(request.data))
        content = '好的 {"text": "嗨", "emotion": {"label": "friendly", "intensity": 0.4}}'
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    settings = Settings(
        openai_model="BIRC-LLM",
        openai_reasoning_effort="none",
        openai_system_role=False,
        openai_json_mode=False,
        _env_file=None,
    )
    service = InteractionService(ModelReplyGenerator(settings, opener))
    prepared = service.prepare(InteractionInput("嗨", Personality()), datetime.now(timezone.utc))
    reply = service.respond(prepared)
    assert reply.source == "model" and reply.text == "嗨"
    assert [message["role"] for message in captured["messages"]] == ["user"]
    content = captured["messages"][0]["content"]
    assert content.startswith(prepared.system_prompt) and content.endswith(prepared.user_prompt)
    assert "response_format" not in captured
    assert captured["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    "text,expected",
    [
        # Stray Simplified characters are converted...
        ("快開冷氣降溫，记得多喝水，别硬撐喔。", "快開冷氣降溫，記得多喝水，別硬撐喔。"),
        # ...whole Simplified words follow context, including shared characters...
        ("这里有点湿，头发都塌了，别一直坐着。", "這裡有點濕，頭髮都塌了，別一直坐著。"),
        ("后来发现一只猫", "後來發現一隻貓"),
        # ...while correct Traditional text, shared characters included, is never rewritten.
        ("環境干擾已套用，台灣、臺北、干涉、乾淨。", "環境干擾已套用，台灣、臺北、干涉、乾淨。"),
    ],
)
def test_stray_simplified_characters_become_traditional(text, expected):
    assert to_traditional(text) == expected


def test_model_reply_text_is_traditional():
    def opener(request, timeout):
        content = json.dumps({"text": "记得喝水，别中暑。", "emotion": {"label": "happy"}})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    service = InteractionService(
        ModelReplyGenerator(Settings(openai_model="m", _env_file=None), opener)
    )
    reply = service.respond(
        service.prepare(InteractionInput("嗨", Personality()), datetime.now(timezone.utc))
    )
    assert reply.text == "記得喝水，別中暑。"


def test_reply_object_survives_braces_in_prose_and_bare_search():
    from memo.infrastructure.model_reply import extract_action

    noisy = '好的 {先想想} 這是回覆：```json\n{"text":"嗨","emotion":{"label":"happy"}}\n```'
    assert parse_content(noisy)[0] == "嗨"
    bare = '{"action":{"type":"search","query":"台北101高度"}}'
    assert parse_content(bare)[0] == "我去查一下。"
    assert extract_action(bare) == {"type": "search", "query": "台北101高度"}
    with pytest.raises(ValueError):
        parse_content('{"action":{"type":"lamp","mode":"off"}}')
