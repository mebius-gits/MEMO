"""Watching a thing, frequent safety checks, and look tasks working with the room's sensors."""

import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from memo.domain.entities import Reading
from memo.domain.schedule import describe, parse_intent, validate_action
from memo.domain.vision import clean_scene, light_jump, too_dark, validate_look
from memo.infrastructure import vision as vision_module
from memo.infrastructure.database import ScheduledJobRow, TaskMemoryRow
from memo.infrastructure.look_sensors import model_hint
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.schedules import fire_due
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import look_words
from memo.infrastructure.vision import notice_context
from memo.infrastructure.vision_model import PriorityGate, VisionFailure
from memo.main import create_app

TZ = "Asia/Taipei"
JPEG = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ff") + b"x" * 64).decode()


# ---- Rules ---------------------------------------------------------------------------------


def watched(text):
    action = parse_intent(text)
    assert action is not None, text
    assert action["look"]["mode"] == "change" and action["look"]["watch"] is True, text
    return action["look"]["question"], action["minutes"]


def test_watching_a_thing_is_set_up_without_the_model():
    assert watched("幫我顧著桌上的箱子") == ("桌上的箱子", 2)
    assert watched("有沒有人動我的東西") == ("我的東西", 2)
    assert watched("幫我看箱子有沒有被人動過") == ("箱子", 2)
    assert watched("你幫我盯著門口，有人進來就說") == ("門口", 2)
    assert watched("桌上的書有沒有被拿走") == ("桌上的書", 2)
    assert watched("每30分鐘幫我看桌上的包裹有沒有被動過") == ("桌上的包裹", 30)
    # A yes/no question about a person stays with the model; so does everyday talk.
    assert parse_intent("幫我盯著我有沒有駝背") is None
    assert parse_intent("盯著螢幕好累") is None
    assert parse_intent("我的書被人動過了") is None
    # Stopping names what to stop watching; with no name the model asks.
    assert parse_intent("不用再盯著箱子了") == {"type": "cancel", "match": "箱子"}
    assert parse_intent("不要顧了") is None


def test_safety_checks_may_be_frequent_only_for_watched_tasks():
    look = {"mode": "change", "question": "箱子", "watch": True}
    base = {"type": "schedule", "kind": "interval", "purpose": "look", "message": "箱子"}
    assert validate_action(base | {"minutes": 1, "look": look})["minutes"] == 1
    assert validate_action(base | {"minutes": 1, "look": look | {"watch": False}}) is None
    assert validate_action(base | {"minutes": 1, "purpose": "remind"}) is None


def test_sensor_tasks_need_a_trigger_and_say_what_starts_them():
    look = {"mode": "check", "question": "窗戶有沒有開？", "on": ["co2_high", "disco", "co2_high"]}
    assert validate_look(look)["on"] == ["co2_high"]
    assert "on" not in validate_look({"question": "x", "on": ["disco"]})
    event = {"type": "schedule", "kind": "event", "purpose": "look", "message": "窗戶"}
    assert validate_action(event | {"look": look})["kind"] == "event"
    assert validate_action(event | {"look": {"question": "窗戶有沒有開？"}}) is None
    assert validate_action(event | {"purpose": "remind"}) is None
    assert describe("event", "look", "窗戶", None, None, None, TZ, {"on": ["co2_high"]}) == (
        "CO₂ 偏高時拍照看看：窗戶"
    )
    assert describe("interval", "look", "箱子", 2, None, None, TZ, {"watch": True}) == (
        "持續盯著：箱子（每 2 分鐘再拍照確認一次）"
    )
    assert describe("interval", "look", "門", 60, None, None, TZ, {"on": ["light"]}).endswith(
        "；燈光變化時也會看"
    )


def test_light_jumps_and_darkness():
    assert light_jump(20, 300) and light_jump(300, 20) and light_jump(0, 40)
    assert not light_jump(200, 260)  # clouds, not a switch
    assert not light_jump(None, 300)
    assert too_dark(["dark"], {"dark": True}) and too_dark(["dark"], {})
    assert not too_dark(["dark"], {"dark": False})  # the room is lit: the camera is off-aim
    assert not too_dark([], {"dark": True})
    hint = model_hint({"light_changed": True, "illuminance_before": 20, "illuminance_lux": 300})
    assert "照度從 20 變成 300 lux" in hint and "光線、陰影與顏色的差異不算變化" in hint
    assert "很暗" in model_hint({"dark": True, "illuminance_lux": 3})


def test_a_watched_change_goes_ahead_of_scheduled_looks():
    gate, order = PriorityGate(), []
    assert gate.acquire(2, 1)
    import threading

    def wait(priority):
        assert gate.acquire(priority, 2)
        order.append(priority)
        gate.release()

    threads = [threading.Thread(target=wait, args=(p,)) for p in (3, 2, 0)]
    for thread in threads:
        thread.start()
    for _ in range(50):
        if len(gate.waiting) == 3:
            break
        threading.Event().wait(0.01)
    gate.release()
    for thread in threads:
        thread.join()
    assert order == [0, 2, 3]
    assert gate.acquire(1, 1) and not gate.acquire(0, 0.05)  # busy: the wait times out
    assert gate.waiting == []


# ---- End to end ----------------------------------------------------------------------------


class Eyes:
    """Answers in order; a VisionFailure in the list is raised instead."""

    unavailable = None
    timeout = 1

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def analyze(self, device_id, look, image, **options):
        self.calls.append(options)
        answer = self.answers.pop(0)
        if isinstance(answer, VisionFailure):
            raise answer
        return {
            "result": answer,
            "detail": f"看到{answer}",
            "visibility": "clear",
            "scene": clean_scene({"objects": ["紙箱"]}),
        }


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'watch.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def eyes(application, *answers):
    fake = Eyes(*answers)
    application.state.capture_service.analyzer = fake
    return fake


def requests(client):
    return client.get("/api/v1/devices/room/captures/pending").json()["requests"]


def capture(client, task_id, attempt=1, **body):
    payload = {"task_id": task_id, "attempt": attempt, "image": JPEG} | body
    return client.post("/api/v1/devices/room/captures", json=payload).json()


def fire(application, minutes=0):
    later = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return fire_due(application.state.session_factory, later, TZ)


def watch_job(client, minutes=2):
    response = client.post(
        "/api/v1/devices/room/schedules",
        json={
            "kind": "interval",
            "minutes": minutes,
            "purpose": "look",
            "message": "箱子",
            "look": {"mode": "change", "question": "桌上的箱子", "watch": True},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def baseline(application, client):
    """Run the task's first photo, which only becomes its baseline."""
    assert fire(application) == 1
    [request] = requests(client)
    assert request["baseline"]
    assert capture(client, request["task_id"])["decision"] == "baseline"


def telemetry(client, lux, at=None, co2=600):
    response = client.post(
        "/api/v1/telemetry",
        json={
            "message_id": uuid4().hex,
            "device_id": "room",
            "sequence": 1,
            "observed_at": (at or datetime.now(timezone.utc)).isoformat(),
            "metrics": {"temperature_c": 25, "illuminance_lux": lux, "co2_ppm": co2},
        },
    )
    assert response.status_code in (200, 201), response.text


def test_chat_sets_up_a_watched_task_and_the_page_watches_it(app):
    application, client = app
    reply = client.post(
        "/api/v1/devices/room/interaction",
        json={"session_id": "s", "request_id": str(uuid4()), "user_message": "幫我顧著桌上的箱子"},
    ).json()
    assert reply["actions"] == ["已設定：持續盯著：桌上的箱子（每 2 分鐘再拍照確認一次）"]
    assert reply["mode"] == "assignment" and reply["look_requested"] is False
    [job] = client.get("/api/v1/devices/room/schedules").json()
    assert job["minutes"] == 2 and job["look"]["watch"] is True and job["look"]["mode"] == "change"
    watching = client.get("/api/v1/devices/room/captures/pending").json()["watching"]
    assert watching == [{"job_id": job["id"], "question": "桌上的箱子", "roi": None}]


def test_safety_checks_do_not_pile_up_behind_a_slow_photo(app):
    application, client = app
    eyes(application, "no")
    watch_job(client, minutes=1)
    assert fire(application) == 1  # the baseline, right away
    # The page has not sent the baseline yet: the next safety checks wait for it.
    assert fire(application, minutes=1) == 0
    assert fire(application, minutes=2) == 0
    [request] = requests(client)
    capture(client, request["task_id"])
    assert fire(application, minutes=3) == 1


def test_the_lamp_and_a_light_jump_make_a_watched_task_look_again(app, monkeypatch):
    application, client = app
    fake = eyes(application, "no", "no", "no")
    job = watch_job(client, minutes=30)
    baseline(application, client)
    # Switching the lamp: MEMO looks again, and tells the model the light just changed.
    client.put("/api/v1/devices/room/lamp", json={"mode": "warm"})
    [request] = requests(client)
    client.put("/api/v1/devices/room/lamp", json={"mode": "day"})  # running: not twice
    assert len(requests(client)) == 1
    result = capture(client, request["task_id"], reference=JPEG)
    assert result["result"] == "no"
    assert "光線、陰影與顏色的差異不算變化" in fake.calls[-1]["hint"]
    record = client.get("/api/v1/devices/room/vision/observations").json()[0]
    assert record["sensors"]["light_changed"] is True
    # The light sensor jumping does the same, once the gap since the last sensor run passed.
    monkeypatch.setattr(vision_module, "SENSOR_GAP", timedelta(0))
    now = datetime.now(timezone.utc)
    telemetry(client, 20, now - timedelta(seconds=10))
    assert requests(client) == []
    telemetry(client, 300, now)
    [request] = requests(client)
    assert request["job_id"] == job["id"]
    # Slowly changing daylight is no trigger.
    capture(client, request["task_id"], reference=JPEG)
    telemetry(client, 330, now + timedelta(seconds=5))
    assert requests(client) == []


def co2_high(application, start):
    """Thirty readings five seconds apart well above the CO₂ limit start the co2_high event."""
    with application.state.session_factory() as session:
        repository = SqlAlchemyRepository(session)
        for i in range(30):
            at = start + timedelta(seconds=5 * i)
            repository.add_reading(
                Reading(
                    message_id=uuid4().hex,
                    device_id="room",
                    observed_at=at,
                    received_at=at,
                    sequence=i,
                    temperature_c=25,
                    humidity_pct=50,
                    co2_ppm=1400,
                    quality="ok",
                    illuminance_lux=250,
                )
            )


def test_an_environment_event_runs_the_task_waiting_for_it(app):
    application, client = app
    eyes(application, "yes", "yes")
    response = client.post(
        "/api/v1/devices/room/schedules",
        json={
            "kind": "event",
            "purpose": "look",
            "message": "窗戶",
            "look": {"question": "窗戶是不是關著？", "on": ["co2_high"]},
        },
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["next_run_at"] is None and job["description"] == "CO₂ 偏高時拍照看看：窗戶"
    assert fire(application, minutes=600) == 0  # no time of its own
    co2_high(application, datetime.now(timezone.utc) - timedelta(minutes=3))
    [request] = requests(client)
    first = capture(client, request["task_id"])
    assert first["decision"] == "confirm"
    second = capture(client, request["task_id"], attempt=2)
    assert second["status"] == "queued"
    with application.state.session_factory() as session:
        job_row = session.get(ScheduledJobRow, job["id"])
        task = session.get(TaskMemoryRow, request["task_id"])
        context = notice_context(session, job_row, task, TZ, datetime.now(timezone.utc))
        assert job_row.status == "active" and job_row.next_run_at is None
    assert context["triggered_by"] == ["CO₂ 偏高"]
    assert context["sensors"]["co2_ppm"] == 1400
    assert look_words(context) == "因為CO₂ 偏高，我去看了一下：看到yes。"


def test_a_watched_change_is_told_when_the_model_cannot_answer(app, monkeypatch):
    application, client = app
    monkeypatch.setattr(vision_module, "TRIGGER_GAP", timedelta(0))  # the baseline just ran
    eyes(application, VisionFailure("model_unavailable"), VisionFailure("model_unavailable"))
    job = watch_job(client)
    # The model is down from the start: no baseline photo could be described.
    assert fire(application) == 1
    [request] = requests(client)
    assert capture(client, request["task_id"])["reason"] == "model_unavailable"
    # The page saw the region change and hold: MEMO says so, as unverified.
    run = client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger").json()
    result = capture(client, run["task_id"], trigger="watch")
    assert (result["decision"], result["notice"], result["store_reference"]) == (
        "notify",
        "changed",
        True,
    )
    record = client.get("/api/v1/devices/room/vision/observations").json()[0]
    assert record["result"] == "yes" and record["reason"] == "model_unavailable"
    assert record["detail"].startswith("盯著的地方在")
    with application.state.session_factory() as session:
        job_row = session.get(ScheduledJobRow, job["id"])
        task = session.get(TaskMemoryRow, run["task_id"])
        context = notice_context(session, job_row, task, TZ, datetime.now(timezone.utc))
    assert context["unverified"] is True
    assert look_words(context).endswith("。我只看到畫面在變，還沒能看清楚是什麼。")


def test_no_unverified_notice_when_the_light_just_changed_or_it_is_dark(app, monkeypatch):
    application, client = app
    monkeypatch.setattr(vision_module, "TRIGGER_GAP", timedelta(0))
    eyes(application, "no", VisionFailure("model_timeout"), "yes")
    job = watch_job(client)
    baseline(application, client)
    # The lamp was just switched: the page probably saw the light, not the box.
    client.put("/api/v1/devices/room/lamp", json={"mode": "dim"})
    with application.state.session_factory() as session:
        for task in session.scalars(select(TaskMemoryRow)).all():
            task.status = "done"  # the lamp's own run is not this test's
        session.commit()
    run = client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger").json()
    result = capture(client, run["task_id"], trigger="watch", reference=JPEG)
    assert (result["decision"], result["result"]) == ("done", "unknown")
    # A dark photo in a dark room cannot say the box moved.
    telemetry(client, 3)
    run = client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger").json()
    dark = {"brightness": 12, "contrast": 30, "sharpness": 100}
    result = capture(client, run["task_id"], trigger="watch", reference=JPEG, quality=dark)
    assert result["result"] == "unknown"
    record = client.get("/api/v1/devices/room/vision/observations").json()[0]
    assert record["detail"] == "太暗了，看不清楚有沒有變" and record["sensors"]["dark"] is True
