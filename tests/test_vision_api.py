"""Look tasks end to end: due run, photo request, confirmation, records, notices, chat."""

import base64
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from memo.application.interaction import InteractionService
from memo.domain.interaction import EmotionLabel, Reply
from memo.domain.vision import clean_scene
from memo.infrastructure.database import ScheduledJobRow, TaskMemoryRow, VisionObservationRow
from memo.infrastructure.notifications import dispatch_one
from memo.infrastructure.schedules import fire_due
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.infrastructure.vision import expire_waiting
from memo.infrastructure.vision_model import VisionFailure
from memo.main import create_app

TZ = "Asia/Taipei"
JPEG = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ff") + b"x" * 64).decode()
OTHER = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ff") + b"y" * 64).decode()
SCENE = {
    "people": {"count": 1, "description": "坐在書桌前"},
    "activity": "用電腦",
    "place": "書桌前",
    "objects": ["筆電", "杯子"],
    "actions": ["打字"],
    "lighting": "明亮",
}


class FakeEyes:
    """Answers in order; a VisionFailure in the list is raised instead."""

    unavailable = None
    timeout = 1

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def analyze(self, device_id, look, image, **options):
        self.calls.append({"image": image, **options})
        answer = self.answers.pop(0)
        if isinstance(answer, VisionFailure):
            raise answer
        return {
            "result": answer,
            "detail": f"看到{answer}",
            "visibility": "clear",
            "scene": clean_scene(SCENE),
        }


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'vision.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def eyes(application, *answers):
    fake = FakeEyes(*answers)
    application.state.capture_service.analyzer = fake
    return fake


def look_job(client, **look):
    response = client.post(
        "/api/v1/devices/room/schedules",
        json={
            "kind": "interval",
            "minutes": 30,
            "purpose": "look",
            "message": "駝背",
            "look": {"question": "使用者是不是駝背？"} | look,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def due(application, minutes=31, **options):
    later = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return fire_due(application.state.session_factory, later, TZ, **options)


def pending(client):
    return client.get("/api/v1/devices/room/captures/pending").json()


def capture(client, task_id, attempt=1, image=JPEG, **body):
    payload = {"task_id": task_id, "attempt": attempt} | body
    if image:
        payload["image"] = image
    return client.post("/api/v1/devices/room/captures", json=payload)


def observations(client, **query):
    return client.get("/api/v1/devices/room/vision/observations", params=query).json()


def test_a_new_situation_is_confirmed_recorded_and_spoken(app):
    application, client = app
    fake = eyes(application, "yes", "yes")
    job = look_job(client)
    assert job["purpose"] == "look" and job["description"].startswith("每 30 分鐘拍照看看")
    assert pending(client) == {"requests": [], "active_jobs": 1, "watching": []}
    assert due(application) == 1
    [request] = pending(client)["requests"]
    assert request["attempt"] == 1 and request["question"] == "使用者是不是駝背？"
    assert not request["baseline"]

    quality = {"brightness": 30, "contrast": 40, "sharpness": 200, "width": 1280, "height": 720}
    first = capture(client, request["task_id"], quality=quality).json()
    assert first["status"] == "confirming" and first["decision"] == "confirm"
    assert first["confirm_after_seconds"] == 6 and first["quality_flags"] == ["dark"]
    [again] = pending(client)["requests"]
    assert again["attempt"] == 2 and again["task_id"] == request["task_id"]
    # Nothing is spoken until the second photo agrees.
    assert client.get("/api/v1/devices/room/notifications").json() == []

    second = capture(client, request["task_id"], attempt=2, image=OTHER).json()
    assert (second["status"], second["notice"], second["level"]) == ("queued", "alert", 1)
    assert [call["image"] for call in fake.calls] == [JPEG, OTHER]
    sessions = application.state.session_factory
    assert dispatch_one(sessions, InteractionService(TemplateReplyGenerator()))
    [notice] = client.get("/api/v1/devices/room/notifications").json()
    assert notice["event_status"] == "look" and notice["response"]["text"] == "提醒一下：看到yes。"
    assert notice["response"]["emotion"]["label"] == "concerned"

    records = observations(client)
    assert [(r["attempt"], r["result"], r["decision"]) for r in records] == [
        (2, "yes", "notify"),
        (1, "yes", "confirm"),
    ]
    assert records[1]["quality"]["flags"] == ["dark"]
    assert records[1]["quality"]["metrics"]["brightness"] == 30
    assert records[0]["scene"]["place"] == "書桌前" and records[0]["scene"]["actions"] == ["打字"]
    state = client.get("/api/v1/devices/room/schedules").json()[0]["look_state"]
    assert state["last"] == "yes" and state["alert_streak"] == 1
    # The photo itself is nowhere in the database.
    with sessions() as session:
        for table in ("vision_observations", "task_memory", "scheduled_jobs", "vision_daily"):
            rows = session.execute(text(f"SELECT * FROM {table}")).all()
            assert "data:image" not in repr(rows) and "eHh4" not in repr(rows)


def test_a_second_photo_that_disagrees_keeps_memo_quiet(app):
    application, client = app
    eyes(application, "yes", "no")
    look_job(client)
    due(application)
    task = pending(client)["requests"][0]["task_id"]
    capture(client, task)
    result = capture(client, task, attempt=2).json()
    assert (result["status"], result["decision"]) == ("done", "unconfirmed")
    assert pending(client)["requests"] == []
    with application.state.session_factory() as session:
        assert session.get(TaskMemoryRow, task).status == "done"
        assert session.scalars(select(ScheduledJobRow)).one().look_state == {}


def test_each_photo_is_taken_once(app):
    application, client = app
    eyes(application, "no")
    look_job(client)
    due(application)
    task = pending(client)["requests"][0]["task_id"]
    assert capture(client, task).json()["status"] == "done"
    assert capture(client, task).status_code == 409  # another tab, or a retry
    assert capture(client, task, attempt=2).status_code == 409
    assert capture(client, "nope").status_code == 404
    both = {"task_id": task, "image": JPEG, "skip": "camera_off"}
    assert client.post("/api/v1/devices/room/captures", json=both).status_code == 422
    neither = {"task_id": task}
    assert client.post("/api/v1/devices/room/captures", json=neither).status_code == 422
    bad = {"task_id": task, "image": "data:image/png;base64,AAAA"}
    assert client.post("/api/v1/devices/room/captures", json=bad).status_code == 422


def test_runs_without_a_photo_are_recorded(app):
    application, client = app
    eyes(application)
    look_job(client)
    sessions = application.state.session_factory
    # The camera is off.
    due(application)
    task = pending(client)["requests"][0]["task_id"]
    skipped = capture(client, task, image=None, skip="camera_off").json()
    assert (skipped["status"], skipped["reason"]) == ("skipped", "camera_off")
    # The page never answers.
    due(application, minutes=61)
    with sessions() as session:
        later = datetime.now(timezone.utc) + timedelta(minutes=64)
        assert expire_waiting(session, later, TZ) == 1
    # Look tasks cannot run without a vision model.
    due(application, minutes=91, vision_unavailable="vision_unavailable")
    reasons = [(r["result"], r["reason"]) for r in observations(client)]
    assert reasons == [
        ("skipped", "vision_unavailable"),
        ("skipped", "no_capture"),
        ("skipped", "camera_off"),
    ]


def test_failed_analysis_is_unknown_and_told_after_three(app):
    application, client = app
    eyes(application, *[VisionFailure("model_timeout")] * 3)
    look_job(client)
    results = []
    for run in range(3):
        due(application, minutes=31 + 30 * run)
        task = pending(client)["requests"][0]["task_id"]
        results.append(capture(client, task, quality={"sharpness": 3}).json())
    assert [r["result"] for r in results] == ["unknown"] * 3
    assert [r["notice"] for r in results] == [None, None, "unseen"]
    assert all(r["reason"] == "model_timeout" for r in results)
    sessions = application.state.session_factory
    assert dispatch_one(sessions, InteractionService(TemplateReplyGenerator()))
    [notice] = client.get("/api/v1/devices/room/notifications").json()
    assert (
        notice["response"]["text"] == "我連續幾次拍照都看不清楚，照片模糊，可以幫我看一下鏡頭嗎？"
    )


def test_change_task_sets_a_baseline_then_compares(app):
    application, client = app
    fake = eyes(application, "no", "no", "yes", "yes")
    job = look_job(client, mode="change", question="桌上的包裹")
    due(application)
    [request] = pending(client)["requests"]
    assert request["baseline"]
    first = capture(client, request["task_id"]).json()
    assert (first["decision"], first["store_reference"]) == ("baseline", True)
    assert fake.calls[0]["baseline"] is True

    due(application, minutes=61)
    [request] = pending(client)["requests"]
    assert not request["baseline"]
    unchanged = capture(client, request["task_id"], reference=OTHER).json()
    assert (unchanged["decision"], unchanged["store_reference"]) == ("done", False)
    assert fake.calls[1]["reference"] == OTHER and fake.calls[1]["baseline"] is False
    assert "書桌前" in fake.calls[1]["baseline_text"]

    due(application, minutes=91)
    task = pending(client)["requests"][0]["task_id"]
    assert capture(client, task, reference=OTHER).json()["decision"] == "confirm"
    changed = capture(client, task, attempt=2, reference=OTHER).json()
    assert (changed["notice"], changed["store_reference"]) == ("changed", True)

    # A new region needs a new reference photo.
    roi = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}
    updated = client.put(f"/api/v1/devices/room/schedules/{job['id']}/look", json={"roi": roi})
    assert updated.json()["look"]["roi"] == roi
    assert updated.json()["look_state"]["baseline_text"] is None


def test_cancelling_a_look_task_forgets_its_records(app):
    application, client = app
    eyes(application, "no")
    job = look_job(client)
    due(application)
    capture(client, pending(client)["requests"][0]["task_id"])
    assert len(observations(client)) == 1
    assert client.delete(f"/api/v1/devices/room/schedules/{job['id']}").status_code == 204
    assert observations(client) == [] and pending(client)["active_jobs"] == 0


def test_look_tasks_need_their_settings(app):
    _, client = app
    body = {"kind": "interval", "minutes": 30, "purpose": "look", "message": "x"}
    assert client.post("/api/v1/devices/room/schedules", json=body).status_code == 422
    remind = {"kind": "once", "minutes": 5, "message": "喝水", "look": {"question": "x"}}
    assert client.post("/api/v1/devices/room/schedules", json=remind).status_code == 422


class Capturing:
    def __init__(self, action=None):
        self.action = action
        self.prepared = []

    def generate(self, prepared):
        self.prepared.append(prepared)
        return Reply("拿到鏡頭前給我看看。", EmotionLabel.FRIENDLY, 0.5, "model", None, self.action)


def chat(client, text, request_id, image=None):
    body = {"session_id": "s", "request_id": request_id, "user_message": text}
    return client.post(
        "/api/v1/devices/room/interaction", json=body | ({"image": image} if image else {})
    ).json()


def test_model_can_ask_to_see_through_the_camera(app):
    application, client = app
    application.state.interaction_service = InteractionService(Capturing({"type": "look"}))
    reply = chat(client, "MEMO 這個是什麼", "00000000-0000-0000-0000-000000000011")
    assert reply["look_requested"] is True and reply["actions"] == []
    # With the photo attached, the model cannot ask for another one.
    reply = chat(client, "MEMO 這個是什麼", "00000000-0000-0000-0000-000000000012", JPEG)
    assert reply["look_requested"] is False and reply["image_attached"] is True


def test_records_reach_chat_only_when_asked_about(app):
    application, client = app
    eyes(application, "yes", "yes")
    look_job(client)
    due(application)
    task = pending(client)["requests"][0]["task_id"]
    capture(client, task)
    capture(client, task, attempt=2)
    generator = Capturing()
    application.state.interaction_service = InteractionService(generator)
    chat(client, "我今天駝背幾次？", "00000000-0000-0000-0000-000000000013")
    chat(client, "晚餐吃什麼好", "00000000-0000-0000-0000-000000000014")
    asked, other = (p.user_prompt for p in generator.prepared)
    assert '"look_tasks": [{' in asked and "書桌前" in asked and '"yes": 1' in asked
    assert '"look_tasks": []' in other
    assert "look_tasks 是你照使用者要求定時拍照" in generator.prepared[0].system_prompt


def test_look_notice_uses_a_short_model_budget(app):
    application, client = app
    eyes(application, "yes", "yes")
    look_job(client)
    due(application)
    task = pending(client)["requests"][0]["task_id"]
    capture(client, task)
    capture(client, task, attempt=2)
    generator = Capturing()
    sessions = application.state.session_factory
    assert dispatch_one(sessions, InteractionService(generator), look_timeout=5)
    [prepared] = generator.prepared
    assert prepared.input.timeout_seconds == 5
    assert prepared.input.schedule["notice"] == "alert"
    assert prepared.input.schedule["scene"].startswith("地點：書桌前")
    assert not prepared.environment_relevant
    with sessions() as session:
        assert session.scalars(select(VisionObservationRow)).all()
