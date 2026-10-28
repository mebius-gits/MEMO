"""MEMO knows it can see, and keeps one watch per thing."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.application.interaction import InteractionService
from memo.domain.interaction import InteractionInput, Personality
from memo.domain.schedule import same_thing, watched_thing
from memo.infrastructure.settings import Settings
from memo.main import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'quiet.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("QUIET_HOURS", "")
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def chat(client, text, **extra):
    return client.post(
        "/api/v1/devices/room/interaction",
        json={"session_id": "s", "request_id": str(uuid4()), "user_message": text} | extra,
    ).json()


def prepare(text, **fields):
    service = InteractionService(generator=None)
    return service.prepare(
        InteractionInput(
            user_message=text,
            personality=Personality(),
            context_source="stored_readings",
            **fields,
        ),
        datetime.now(timezone.utc),
    )


# ---- Seeing --------------------------------------------------------------------------------


def test_the_prompt_knows_the_camera_and_asks_for_a_photo():
    prepared = prepare("我現在穿什麼顏色", camera="off", mode="interaction")
    assert json.loads(prepared.user_prompt)["camera"] == "off"
    assert "我穿什麼" in prepared.system_prompt and '{"type":"look"}' in prepared.system_prompt
    assert "鏡頭和感測器兩邊都要說到" in prepared.system_prompt
    assert json.loads(prepare("hi").user_prompt)["camera"] is None


def test_the_page_tells_the_camera_state(app):
    _, client = app
    reply = chat(client, "你好", camera="on")
    assert reply["text"]
    assert chat(client, "你好", camera="maybe").get("detail")  # only on / off


def test_watching_the_same_thing_twice_keeps_one_watch(app):
    _, client = app
    first = chat(client, "幫我顧著這箱子")
    assert first["actions"] == ["已設定：持續盯著：這箱子（每 2 分鐘再拍照確認一次）"]
    again = chat(client, "幫我看桌上的箱子有沒有被人動過")
    assert again["actions"] == ["已經在盯了：持續盯著：這箱子（每 2 分鐘再拍照確認一次）"]
    assert len(client.get("/api/v1/devices/room/schedules").json()) == 1
    # Another thing is another watch.
    chat(client, "幫我顧著門口的包裹")
    assert len(client.get("/api/v1/devices/room/schedules").json()) == 2


def test_a_watch_is_about_the_thing_not_the_question():
    assert watched_thing("看桌上的箱子有沒有被動過") == "桌上的箱子"
    assert watched_thing("箱子") == "箱子"
    assert same_thing("這箱子", "桌上的箱子")
    assert same_thing("我的筆電", "這台筆電")
    assert not same_thing("桌上的箱子", "門口的箱子")


def test_a_question_about_the_camera_leaves_the_room_out():
    asked = dict(mode="assignment", vision=({"question": "桌上的箱子"},))
    assert prepare("箱子剛剛有被動過嗎", **asked).environment_relevant is False
    assert prepare("剛剛有人動箱子嗎？房間好悶", **asked).environment_relevant is True
    assert prepare("現在這邊怎樣", **asked).environment_relevant is True
