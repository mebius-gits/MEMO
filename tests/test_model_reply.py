import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from memo.application.interaction import InteractionService
from memo.domain.interaction import InteractionInput, Personality
from memo.infrastructure.model_reply import ModelReplyGenerator
from memo.infrastructure.settings import Settings


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
        llm_base_url="http://model.local/v1",
        llm_model="test-model",
        llm_api_key="fake-test-key",
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
        Settings(llm_base_url="http://model/v1", llm_model="test", _env_file=None), opener
    )
    service = InteractionService(generator)
    result = service.respond(
        service.prepare(InteractionInput("Hi", Personality()), datetime.now(timezone.utc))
    )
    assert result.source == "template" and result.fallback_reason == reason
