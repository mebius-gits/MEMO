"""Schedules in seconds, 「還在不在」 presence watches, and watching the whole space with the
room's sensors."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.domain.schedule import describe, following_run, parse_intent, validate_action
from memo.infrastructure import vision as vision_module
from memo.infrastructure.schedules import fire_due
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import look_words
from memo.infrastructure.vision import start_triggered
from memo.main import create_app
from tests.test_watch_iot import JPEG, Eyes

TZ = "Asia/Taipei"
LOOK = {"type": "schedule", "kind": "interval", "purpose": "look", "message": "x"}


# ---- Rules ---------------------------------------------------------------------------------


def test_reminders_and_delays_in_seconds():
    assert parse_intent("10秒後提醒我喝水") == {
        "type": "schedule",
        "kind": "once",
        "purpose": "remind",
        "seconds": 10,
        "message": "喝水",
    }
    assert parse_intent("半分鐘後提醒我關火")["seconds"] == 30
    assert parse_intent("120秒後提醒我喝水")["minutes"] == 2  # whole minutes stay minutes
    assert parse_intent("2分鐘後提醒我喝水")["minutes"] == 2
    # A spoken reminder repeating every few seconds would be noise.
    assert parse_intent("每30秒提醒我喝水") is None
    base = {"type": "schedule", "kind": "once", "message": "喝水"}
    assert validate_action(base | {"seconds": 4}) is None
    assert validate_action(base | {"seconds": 5})["seconds"] == 5


def test_frequent_look_tasks_are_watched():
    every = LOOK | {"seconds": 30}
    check = validate_action(every | {"look": {"question": "門有沒有關？", "alert_on": "no"}})
    assert check["look"]["watch"] is True and check["seconds"] == 30
    # A report every 30 seconds would talk all the time: it tells what changes instead.
    report = validate_action(every | {"look": {"mode": "report", "question": "這裡的狀態"}})
    assert report["look"]["mode"] == "change" and report["look"]["watch"] is True
    assert report["look"]["question"] == "這裡的狀態"
    assert validate_action(LOOK | {"seconds": 5, "look": {"question": "門？"}}) is None
    # Every 5 minutes or more stays an ordinary task.
    slow = validate_action(LOOK | {"minutes": 30, "look": {"question": "門有沒有關？"}})
    assert slow["look"]["watch"] is False


def test_presence_is_asked_again_and_again():
    keys = parse_intent("每30秒幫我看鑰匙還在不在")
    assert keys["seconds"] == 30 and keys["message"] == "鑰匙"
    look = keys["look"]
    assert (look["mode"], look["question"], look["alert_on"], look["watch"]) == (
        "check",
        "鑰匙還在嗎？",
        "no",
        True,
    )
    assert parse_intent("幫我盯著錢包還在不在")["look"]["question"] == "錢包還在嗎？"
    assert parse_intent("每10分鐘看一下錢包還在不在")["minutes"] == 10
    # A question about right now is not a task: the page takes a photo for it.
    assert parse_intent("我的鑰匙還在桌上嗎") is None
    assert parse_intent("鑰匙還在嗎") is None


def test_watching_the_space_listens_to_the_air():
    space = parse_intent("每30秒幫我觀看這裡狀態")
    assert space["seconds"] == 30 and space["look"]["question"] == "這裡"
    assert space["look"]["mode"] == "change" and space["look"]["on"] == ["co2_high", "co2_rising"]
    assert parse_intent("幫我注意家裡")["look"]["on"] == ["co2_high", "co2_rising"]
    assert parse_intent("幫我顧著桌上的箱子")["look"].get("on") is None  # a thing, not a space
    assert parse_intent("看一下房間") is None  # a look right now (REPORT_NOW)


def test_seconds_in_timing_and_words():
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    after = following_run("interval", None, None, now, now + timedelta(seconds=5), TZ, 30)
    assert after == now + timedelta(seconds=30)
    keys = {"mode": "check", "question": "鑰匙還在嗎？", "watch": True}
    assert (
        describe("interval", "look", "鑰匙", None, None, now, TZ, keys, 30)
        == "持續盯著：鑰匙還在嗎？（每 30 秒拍照確認一次）"
    )
    assert describe("once", "remind", "喝水", None, None, now, TZ, seconds=10).startswith(
        "10 秒後（16:00）提醒：喝水"
    )


def test_a_change_notice_says_what_the_sensors_add():
    schedule = {
        "notice": "changed",
        "detail": "桌前多了一個人",
        "sensors": {"light_changed": True, "co2_change_10min": 180},
    }
    assert look_words(schedule) == "我拍照比對發現有變化：桌前多了一個人，燈光剛變過，CO₂ 也在升。"


# ---- End to end ----------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'seconds.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def chat(client, text):
    return client.post(
        "/api/v1/devices/room/interaction",
        json={"session_id": "s", "request_id": str(uuid4()), "user_message": text},
    ).json()


def test_keys_every_30_seconds_end_to_end(app, monkeypatch):
    application, client = app
    monkeypatch.setattr(vision_module, "TRIGGER_GAP", timedelta(0))  # the last run just ended
    fake = Eyes("yes", "no", "no")
    application.state.capture_service.analyzer = fake
    reply = chat(client, "每30秒幫我看鑰匙還在不在")
    assert reply["actions"] == ["已設定：持續盯著：鑰匙還在嗎？（每 30 秒拍照確認一次）"]
    [job] = client.get("/api/v1/devices/room/schedules").json()
    assert job["seconds"] == 30 and job["minutes"] is None
    # The page watches its region, and runs the check the moment the region changes.
    watching = client.get("/api/v1/devices/room/captures/pending").json()["watching"]
    assert watching == [{"job_id": job["id"], "question": "鑰匙還在嗎？", "roi": None}]
    due = datetime.fromisoformat(job["next_run_at"]) - datetime.fromisoformat(job["created_at"])
    assert timedelta(seconds=29) <= due <= timedelta(seconds=31)

    def run():
        triggered = client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger")
        assert triggered.status_code == 200, triggered.text
        body = {"task_id": triggered.json()["task_id"], "attempt": 1, "image": JPEG}
        return client.post("/api/v1/devices/room/captures", json=body | {"trigger": "watch"}).json()

    assert run()["decision"] == "done"  # the keys are there
    gone = run()
    # The page saw the change hold, so the first photo is trusted and MEMO speaks at once.
    assert (gone["decision"], gone["status"]) == ("notify", "queued")
    # A new pace for the same watch changes it instead of adding another.
    again = chat(client, "改成每10秒幫我看鑰匙還在不在")
    assert again["actions"] == ["已改成：持續盯著：鑰匙還在嗎？（每 10 秒拍照確認一次）"]
    assert len(client.get("/api/v1/devices/room/schedules").json()) == 1
    # The scheduler runs the safety photo on the 10-second pace.
    sessions, now = application.state.session_factory, datetime.now(timezone.utc)
    assert fire_due(sessions, now + timedelta(seconds=11), TZ) == 1


def test_the_space_watch_looks_when_co2_rises(app):
    application, client = app
    chat(client, "每30秒幫我觀看這裡狀態")
    with application.state.session_factory() as session:
        started = start_triggered(session, "room", ["co2_rising"], datetime.now(timezone.utc))
        session.commit()
    assert started == 1
    assert len(client.get("/api/v1/devices/room/captures/pending").json()["requests"]) == 1
