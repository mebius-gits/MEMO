"""Everyday chat leaves the room out; it comes back when the person brings it up."""

import json
from datetime import datetime, timezone

import pytest

from memo.application.interaction import InteractionService
from memo.domain.interaction import EventContext, InteractionInput, Observation, Personality
from memo.infrastructure.template_reply import TemplateReplyGenerator

NOW = datetime.now(timezone.utc)
READINGS = Observation(NOW, {"temperature_c": 26.5, "humidity_pct": 60, "co2_ppm": 1300}, "ok")
EVENT = EventContext("evt", 3, "active", "co2_high: server validated")


def prepare(message, history=(), level="caution", proactive=False):
    service = InteractionService(TemplateReplyGenerator())
    request = InteractionInput(
        message,
        Personality(),
        observation=READINGS,
        event=EVENT,
        device_id="room01",
        context_source="stored_readings",
        attention={"rule": "co2_high", "level": level, "level_label": "注意"},
        approved_suggestions=("讓空氣流通",),
        history=tuple(history),
        proactive=proactive,
    )
    prepared = service.prepare(request, NOW)
    return prepared, json.loads(prepared.user_prompt), service.respond(prepared)


@pytest.mark.parametrize("message", ["你喜歡玩什麼遊戲？", "今天好累喔", "你多少歲"])
def test_everyday_chat_leaves_the_room_out(message):
    prepared, data, reply = prepare(message)
    assert data["environment"] == "omitted" and not prepared.environment_relevant
    assert data["usable_metrics"] == {} and data["event"] is None and data["attention"] is None
    assert data["approved_suggestions"] == [] and data["warnings"] == []
    assert "朋友，不是監控面板" in prepared.system_prompt
    # Even the fallback stays out of the room (it used to read out numbers for 「多少」).
    assert "讀值" not in reply.text and "1300" not in reply.text


@pytest.mark.parametrize(
    "message,history",
    [
        ("房間好悶喔", ()),
        ("現在幾度？", ()),
        ("燈好暗", ()),
        (
            "那現在呢？",
            (
                {"role": "user", "content": "現在 CO2 多少"},
                {"role": "assistant", "content": "1300 ppm"},
            ),
        ),
    ],
)
def test_the_room_comes_back_when_it_is_the_topic(message, history):
    prepared, data, _ = prepare(message, history)
    assert data["environment"] == "included" and prepared.environment_relevant
    assert data["usable_metrics"]["co2_ppm"] == 1300 and data["event"]["event_id"] == "evt"


def test_danger_and_proactive_notices_always_carry_the_room():
    _, data, _ = prepare("你喜歡玩什麼遊戲？", level="danger")
    assert data["environment"] == "included" and data["attention"]["level"] == "danger"
    _, data, _ = prepare("狀況已經恢復，用一句話告訴使用者。", proactive=True)
    assert data["environment"] == "included"
