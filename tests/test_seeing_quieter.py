"""MEMO knows it can see, keeps one watch per thing, and does not repeat itself about the room."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from memo.application.interaction import InteractionService
from memo.domain.entities import Reading
from memo.domain.interaction import EmotionLabel, InteractionInput, Personality, Reply
from memo.domain.schedule import parse_intent, same_thing, watched_thing
from memo.infrastructure.database import EventUpdateRow, TaskMemoryRow
from memo.infrastructure.repository import SqlAlchemyRepository
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


def test_monitoring_words_set_up_a_watch():
    assert parse_intent("幫我監控水壺")["look"]["question"] == "水壺"
    assert parse_intent("幫我監視門口")["look"]["watch"] is True
    assert parse_intent("每30秒幫我監控水壺")["seconds"] == 30
    assert parse_intent("開始監控")["look"]["on"] == ["co2_high", "co2_rising"]  # the space
    assert parse_intent("不用再監控水壺了") == {"type": "cancel", "match": "水壺"}
    assert parse_intent("我在監控我的血糖") is None  # talk, not a request


def test_a_task_said_to_be_set_up_must_exist(app):
    application, client = app

    class Claiming:
        """Says it set something up but sends no action, as the model sometimes does."""

        def generate(self, prepared):
            return Reply("好，已經幫你建立監控任務了！", EmotionLabel.HAPPY, 0.5, "model", None)

    application.state.interaction_service = InteractionService(Claiming())
    reply = chat(client, "幫我留意水壺")
    assert reply["actions"] == [] and "還沒設定成功" in reply["text"]
    assert client.get("/api/v1/devices/room/schedules").json() == []
    # Said by the rules, the watch exists, and the reply may say so.
    reply = chat(client, "幫我監控水壺")
    assert reply["actions"] == ["已設定：持續盯著：水壺（每 2 分鐘再拍照確認一次）"]
    assert "還沒設定成功" not in reply["text"]


def test_tasks_can_be_deleted_by_voice(app):
    _, client = app
    chat(client, "幫我監控水壺")
    chat(client, "10分鐘後提醒我喝水")
    chat(client, "幫我顧著鑰匙")
    assert len(client.get("/api/v1/devices/room/schedules").json()) == 3
    assert chat(client, "刪掉水壺的任務")["actions"] == ["已取消：水壺"]
    assert chat(client, "把鑰匙任務刪掉")["actions"] == ["已取消：鑰匙"]
    left = client.get("/api/v1/devices/room/schedules").json()
    assert [job["message"] for job in left] == ["喝水"]
    # Nothing by that name: MEMO says so instead of sounding done.
    reply = chat(client, "刪掉錢包的任務")
    assert reply["actions"] == [] and "沒找到「錢包」" in reply["text"]
    assert parse_intent("停止監控") is None  # names nothing: the model asks which
    assert chat(client, "刪除所有任務")["actions"] == ["已取消：喝水"]
    assert client.get("/api/v1/devices/room/schedules").json() == []
    assert "目前沒有任務" in chat(client, "刪除所有任務")["text"]


def test_a_question_about_the_camera_leaves_the_room_out():
    asked = dict(mode="assignment", vision=({"question": "桌上的箱子"},))
    assert prepare("箱子剛剛有被動過嗎", **asked).environment_relevant is False
    assert prepare("剛剛有人動箱子嗎？房間好悶", **asked).environment_relevant is True
    assert prepare("現在這邊怎樣", **asked).environment_relevant is True


# ---- Quieter about the room ----------------------------------------------------------------


def feed(application, start, values, every=5, deliver=True):
    """CO₂ readings; with ``deliver`` the page shows each notice as soon as it is ready."""
    with application.state.session_factory() as session:
        repository = SqlAlchemyRepository(session)
        for i, co2 in enumerate(values):
            at = start + timedelta(seconds=every * i)
            repository.add_reading(
                Reading(
                    message_id=uuid4().hex,
                    device_id="room",
                    observed_at=at,
                    received_at=at,
                    sequence=i,
                    temperature_c=25,
                    humidity_pct=50,
                    co2_ppm=co2,
                    quality="ok",
                )
            )
            for row in session.scalars(
                select(TaskMemoryRow).where(TaskMemoryRow.status == "queued")
            ):
                row.status = "acknowledged" if deliver else row.status
            session.commit()
    return start + timedelta(seconds=every * len(values))


def notices(application):
    with application.state.session_factory() as session:
        rows = session.scalars(select(TaskMemoryRow).order_by(TaskMemoryRow.created_at)).all()
        return [(row.rule_id, row.event_status) for row in rows if row.status != "cancelled"]


def test_co2_rising_then_high_is_said_once(app):
    application, _ = app
    start = datetime.now(timezone.utc) - timedelta(minutes=8)
    feed(application, start, [800] * 14 + [1400] * 40)
    assert notices(application) == [("co2_rising", "active")]
    with application.state.session_factory() as session:
        logged = session.scalars(
            select(EventUpdateRow).where(EventUpdateRow.rule_id == "co2_high")
        ).one()
        assert logged.kind == "active" and logged.detail["spoken"] is False


def test_an_all_clear_needs_a_heard_notice_and_trends_ease_quietly(app):
    application, _ = app
    start = datetime.now(timezone.utc) - timedelta(minutes=20)
    after = feed(application, start, [800] * 14 + [1400] * 40)
    # CO₂ high recovers: said, since the rise was heard. The rise easing off is not.
    feed(application, after, [900] * 40)
    assert notices(application) == [("co2_rising", "active"), ("co2_high", "resolved")]


def test_an_unheard_event_recovers_quietly(app):
    application, _ = app
    start = datetime.now(timezone.utc) - timedelta(minutes=20)
    after = feed(application, start, [1400] * 40, deliver=False)
    assert notices(application) == [("co2_high", "active")]
    feed(application, after, [900] * 40, deliver=False)  # the page was closed
    assert notices(application) == []
