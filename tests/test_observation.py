"""MEMO's own observation: situation, log, what is worth saying, assignments and reports."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

import memo.infrastructure.observation as observation_module
import memo.infrastructure.vision as vision_module
from memo.application.interaction import InteractionService
from memo.domain.attention import AttentionPolicy
from memo.domain.interaction import EmotionLabel, InteractionInput, Personality, Reply
from memo.domain.observation import (
    animal_kind,
    asks_for_report,
    changes,
    conversation_mode,
    light_level,
    nobody_here,
    remember,
    situation,
    worth_saying,
)
from memo.domain.vision import clean_scene, judge, parse_observation, validate_look
from memo.infrastructure.database import (
    EventUpdateRow,
    ObservationStateRow,
    ScheduledJobRow,
    TaskMemoryRow,
)
from memo.infrastructure.environment_events import record
from memo.infrastructure.notifications import dispatch_one
from memo.infrastructure.schedules import fire_due
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.infrastructure.vision_model import VisionFailure
from memo.main import create_app

TZ = "Asia/Taipei"
NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
JPEG = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ff") + b"x" * 64).decode()
MOMENT = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ff") + b"m" * 64).decode()


def scene(people=1, animals=(), lighting="明亮"):
    return clean_scene(
        {
            "people": {"count": people, "description": "坐在書桌前" if people else ""},
            "animals": list(animals),
            "place": "書桌前",
            "objects": ["筆電", "紙箱"],
            "actions": ["打字"] if people else [],
            "lighting": lighting,
        }
    )


# ---- Pure parts -------------------------------------------------------------------------


def test_animals_and_light_are_compared_by_kind():
    assert animal_kind("一隻貓（趴在箱子上）") == "貓"
    assert animal_kind("兩隻狗在睡覺") == "狗"
    assert animal_kind("鸚鵡") == "鸚鵡"
    assert light_level("昏暗") == "dark" and light_level("明亮") == "bright"
    assert light_level("逆光") is None and light_level("") is None


def test_changes_between_situations():
    first = situation(scene(1), "有人在打字", NOW)
    assert [e["kind"] for e in changes(None, first)] == ["start"]
    empty = situation(scene(0, lighting="昏暗"), "沒人", NOW)
    assert [e["kind"] for e in changes(first, empty)] == ["departure", "lights"]
    back = situation(scene(1, ["一隻貓（趴在箱子上）"]), "人和貓", NOW)
    events = changes(empty, back)
    assert [e["kind"] for e in events] == ["arrival", "animal", "lights"]
    assert events[1]["animal"] == "貓" and events[1]["detail"] == "一隻貓（趴在箱子上）"
    # The same cat in another pose is not a new animal; leaving is noted.
    assert changes(back, situation(scene(1, ["一隻貓在睡覺"]), "", NOW)) == []
    assert [e["kind"] for e in changes(back, situation(scene(1), "", NOW))] == ["animal_gone"]
    # An unknown head count (the model could not tell) is not a coming or going.
    unknown = situation(clean_scene({"people": {"description": "看不清楚"}}), "", NOW)
    assert changes(first, unknown) == []


def test_what_is_worth_saying():
    arrival = {"kind": "arrival", "detail": ""}
    cat = {"kind": "animal", "detail": "一隻貓", "animal": "貓"}
    memory = remember({}, [{"kind": "departure"}], [], NOW)
    assert not worth_saying(arrival, memory, NOW + timedelta(minutes=5))  # a short absence
    assert worth_saying(arrival, memory, NOW + timedelta(minutes=12))
    assert not worth_saying(arrival, {}, NOW)  # never saw them leave
    assert worth_saying(cat, {}, NOW)
    memory = remember({}, [cat], [cat], NOW)
    assert not worth_saying(cat, memory, NOW + timedelta(minutes=10))
    assert worth_saying(cat, memory, NOW + timedelta(minutes=31))
    assert not worth_saying({"kind": "lights", "detail": ""}, {}, NOW)


def test_nobody_here_needs_a_recent_look():
    seen_empty = situation(scene(0), "", NOW)
    assert nobody_here(seen_empty, NOW + timedelta(minutes=3))
    assert not nobody_here(seen_empty, NOW + timedelta(minutes=11))  # too old to know
    assert not nobody_here(situation(scene(1), "", NOW), NOW)
    assert not nobody_here(None, NOW)


@pytest.mark.parametrize(
    "text,mode",
    [
        ("幫我顧著箱子", "assignment"),
        ("現在這邊怎樣？", "assignment"),
        ("今天有誰來過", "assignment"),
        ("每 30 分鐘告訴我這邊的情況", "assignment"),
        ("現在 CO2 多少", "assignment"),
        ("房間好亂，不想整理", "interaction"),
        ("我盯著這題半小時了", "interaction"),
        ("明天要報告，我還沒開始", "interaction"),
        ("我最近情況不太好", "interaction"),
        ("今天做得好爛", "interaction"),
    ],
)
def test_assignment_or_interaction(text, mode):
    assert conversation_mode(text) == mode


def test_report_now_phrases():
    assert asks_for_report("現在這邊怎樣") and asks_for_report("幫我看看四周")
    assert not asks_for_report("我手上拿的是什麼")


def test_report_and_watch_tasks():
    report = validate_look({"mode": "report", "question": "這邊的情況", "notify": "on_alert"})
    assert report["notify"] == "always" and report["watch"] is False
    # A check can be watched too (「鑰匙還在嗎？」 asked again when the region changes).
    assert validate_look({"mode": "check", "question": "x", "watch": True})["watch"] is True
    assert validate_look({"mode": "report", "question": "x", "watch": True})["watch"] is False
    watched = validate_look({"mode": "change", "question": "箱子", "watch": True})
    assert watched["watch"] is True
    parsed = parse_observation(
        {"summary": "一隻貓在箱子上", "scene": {"animals": ["貓"]}}, "report"
    )
    assert parsed["result"] == "seen" and parsed["scene"]["animals"] == ["貓"]
    assert parse_observation({"summary": "", "answer": "unknown"}, "report")["result"] == "unknown"
    with pytest.raises(ValueError):
        parse_observation({"answer": "yes"}, "report")
    verdict = judge(report, {}, "seen", "t", scene_line="貓在箱子上")
    assert (verdict.decision, verdict.notice) == ("notify", "report")
    # A watched change was already seen to hold: no confirming photo.
    base = judge(watched, {}, "no", "t0", baseline=True, scene_line="箱子在右邊").state
    assert judge(watched, base, "yes", "t1").decision == "confirm"
    told = judge(watched, base, "yes", "t1", confirmed=True, scene_line="箱子在左邊")
    assert (told.decision, told.notice, told.store_reference) == ("notify", "changed", True)


def test_persona_and_modes_in_the_prompt():
    service = InteractionService(TemplateReplyGenerator())
    chat = service.prepare(
        InteractionInput("房間好亂", Personality(), mode="interaction", situation={"now": None}),
        NOW,
    )
    assert "衣服山" in chat.system_prompt and "少年" not in chat.system_prompt
    assert "朋友，不是監控面板" in chat.system_prompt
    data = json.loads(chat.user_prompt)
    assert data["mode"] == "interaction" and data["situation"] is None
    report = service.prepare(
        InteractionInput(
            "現在這邊怎樣", Personality(), mode="assignment", image=JPEG, situation={"now": None}
        ),
        NOW,
    )
    assert "回報這邊現在的情況" in report.system_prompt
    assert json.loads(report.user_prompt)["situation"] == {"now": None}
    assert report.environment_relevant


def test_nobody_seen_keeps_environment_notices_in_the_log(app):
    application, _ = app
    policy = AttentionPolicy()
    with application.state.session_factory() as session:
        session.add(
            ObservationStateRow(
                device_id="room",
                state=situation(scene(0), "沒人", datetime.now(timezone.utc)),
                memory={},
                updated_at=datetime.now(timezone.utc),
            )
        )
        now = datetime.now(timezone.utc)
        for rule, level in (("co2_high", "caution"), ("temperature_high", "danger")):
            state = {"event_id": f"evt-{rule}", "version": 0, "level": level, "status": "active"}
            record(session, None, "room", rule, {}, state, "active", "active", now, policy)
        session.commit()
        spoken = {
            row.rule_id: row.detail["spoken"]
            for row in session.scalars(
                select(EventUpdateRow).where(EventUpdateRow.kind == "active")
            )
        }
        assert spoken == {"co2_high": False, "temperature_high": True}  # danger still speaks
        assert {task.rule_id for task in session.scalars(select(TaskMemoryRow))} == {
            "temperature_high"
        }


# ---- Through the API --------------------------------------------------------------------


class FakeEyes:
    unavailable = None
    timeout = 1

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def analyze(self, device_id, look, image, **options):
        self.calls.append({"look": look, "image": image, **options})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def seen(people=1, animals=(), summary="有人在書桌前打字", lighting="明亮"):
    return {
        "result": "seen",
        "detail": summary,
        "visibility": "clear",
        "scene": scene(people, animals, lighting),
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'observe.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def eyes(application, *answers):
    fake = FakeEyes(*answers)
    application.state.observation_service.analyzer = fake
    application.state.capture_service.analyzer = fake
    return fake


def observe(client, trigger="patrol", **body):
    payload = {"trigger": trigger} | ({"image": JPEG} if "skip" not in body else {}) | body
    return client.post("/api/v1/devices/room/observe", json=payload)


def no_throttle(monkeypatch):
    monkeypatch.setattr(observation_module, "MIN_GAP_SECONDS", 0)
    monkeypatch.setattr(observation_module, "PERSON_GAP_SECONDS", 0)


def notices(application, client):
    sessions = application.state.session_factory
    while dispatch_one(sessions, InteractionService(TemplateReplyGenerator())):
        pass
    return [n["response"]["text"] for n in client.get("/api/v1/devices/room/notifications").json()]


def test_memo_watches_the_space_and_greets_after_an_absence(app, monkeypatch):
    application, client = app
    no_throttle(monkeypatch)
    fake = eyes(
        application,
        seen(1),
        seen(0, summary="書桌前沒人", lighting="昏暗"),
        seen(0, ["一隻貓（跳上桌子）"], summary="一隻貓跳上桌子"),
        seen(1, ["一隻貓（趴在箱子上）"], summary="人回來了，貓在箱子上"),
    )
    first = observe(client).json()
    assert (first["result"], first["events"], first["notice"]) == ("seen", ["start"], None)
    assert fake.calls[0]["look"]["mode"] == "report"
    left = observe(client, "person_leave").json()
    assert left["events"] == ["departure", "lights"] and left["notice"] is None
    cat = observe(client, "scene_change").json()
    assert (cat["events"], cat["notice"]) == (["animal", "lights"], "animal")
    assert notices(application, client) == ["我看到一隻貓（跳上桌子）了。"]
    # Pretend the person left 15 minutes ago.
    with application.state.session_factory() as session:
        row = session.get(ObservationStateRow, "room")
        away = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        row.memory = row.memory | {"empty_since": away}
        session.commit()
    back = observe(client, "person_enter").json()
    assert (back["events"], back["notice"]) == (["arrival"], "arrival")
    [greeting] = [text for text in notices(application, client) if text.startswith("你回來啦")]
    assert "動物出現：一隻貓（跳上桌子）" in greeting
    state = client.get("/api/v1/devices/room/observation").json()
    assert state["now"]["people"] == 1 and state["now"]["animals"] == ["一隻貓（趴在箱子上）"]
    assert state["today_counts"]["有人出現"] == 1 and state["patrol_minutes"] == 15
    log = client.get("/api/v1/devices/room/observation/log").json()
    assert log[0]["kind"] == "arrival" and {e["kind"] for e in log[1:3]} == {"animal", "lights"}
    assert next(e for e in log if e["kind"] == "animal")["spoken"] is True
    # Every photo is recorded like a look task's, with its scene; none is stored.
    records = client.get("/api/v1/devices/room/vision/observations", params={"job_id": "observe"})
    assert len(records.json()) == 4 and records.json()[0]["scene"]["animals"]
    assert client.delete("/api/v1/devices/room/observation/log").status_code == 204
    assert client.get("/api/v1/devices/room/observation").json()["now"] is None


def test_observation_is_paced_and_quiet_hours_only_record(app, monkeypatch):
    application, client = app
    eyes(application, seen(0), seen(0, ["一隻狗"]), VisionFailure("model_timeout"))
    assert observe(client).status_code == 200
    assert observe(client).status_code == 409  # a patrol right after a look
    no_throttle(monkeypatch)
    client.put("/api/v1/devices/room/settings", json={"quiet_hours": "00:00-23:59"})
    dog = observe(client, "scene_change").json()
    assert dog["events"] == ["animal"] and dog["notice"] is None  # logged, not said
    failed = observe(client).json()
    assert (failed["result"], failed["reason"]) == ("unknown", "model_timeout")
    off = observe(client, skip="camera_off").json()
    assert (off["result"], off["reason"]) == ("skipped", "camera_off")


def test_chat_tells_assignment_from_interaction(app):
    application, client = app

    class Capturing:
        def __init__(self):
            self.prepared = []

        def generate(self, prepared):
            self.prepared.append(prepared)
            return Reply("好。", EmotionLabel.FRIENDLY, 0.5, "model", None, None)

    generator = Capturing()
    application.state.interaction_service = InteractionService(generator)
    body = {"session_id": "s", "user_message": "房間好亂，不想整理"}
    assert client.post("/api/v1/devices/room/interaction", json=body).json()["mode"] == (
        "interaction"
    )
    body = {"session_id": "s", "user_message": "現在這邊怎樣？", "image": JPEG}
    reply = client.post("/api/v1/devices/room/interaction", json=body).json()
    assert reply["mode"] == "assignment"
    chat, report = (json.loads(p.user_prompt) for p in generator.prepared)
    assert chat["situation"] is None and chat["environment"] == "omitted"
    assert report["situation"]["now"] is None and report["environment"] == "included"


def look_job(client, **look):
    response = client.post(
        "/api/v1/devices/room/schedules",
        json={"kind": "interval", "minutes": 30, "purpose": "look", "message": "x", "look": look},
    )
    assert response.status_code == 201, response.text
    return response.json()


def due(application, minutes=0):
    later = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return fire_due(application.state.session_factory, later, TZ)


def capture(client, task_id, attempt=1, **body):
    payload = {"task_id": task_id, "attempt": attempt, "image": JPEG} | body
    return client.post("/api/v1/devices/room/captures", json=payload)


def pending(client):
    return client.get("/api/v1/devices/room/captures/pending").json()


def changed(result, detail="箱子被移到左邊"):
    return {"result": result, "detail": detail, "visibility": "clear", "scene": scene(0)}


def test_a_watched_box_is_baselined_now_and_changes_are_told_at_once(app, monkeypatch):
    application, client = app
    fake = eyes(application, changed("no", "箱子在右邊"), changed("yes"))
    job = look_job(client, mode="change", question="桌上的箱子", watch=True)
    assert job["look"]["watch"] is True
    assert pending(client)["watching"] == [
        {"job_id": job["id"], "question": "桌上的箱子", "roi": None}
    ]
    # The baseline is taken right away, not after the first interval.
    assert due(application) == 1
    [request] = pending(client)["requests"]
    assert request["baseline"]
    assert capture(client, request["task_id"]).json()["decision"] == "baseline"
    # Quiet hours do not stop a watched object from being told.
    client.put("/api/v1/devices/room/settings", json={"quiet_hours": "00:00-23:59"})
    monkeypatch.setattr(vision_module, "TRIGGER_GAP", timedelta(0))  # the baseline just ran
    triggered = client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger")
    assert triggered.status_code == 200, triggered.text
    run = triggered.json()
    result = capture(client, run["task_id"], trigger="watch", moment=MOMENT, reference=JPEG).json()
    assert (result["decision"], result["notice"]) == ("notify", "changed")  # no second photo
    assert fake.calls[1]["moment"] == MOMENT
    assert notices(application, client) == ["我拍照比對發現有變化：箱子被移到左邊。"]
    # Not again right away.
    monkeypatch.setattr(vision_module, "TRIGGER_GAP", timedelta(seconds=20))
    assert client.post(f"/api/v1/devices/room/schedules/{job['id']}/trigger").status_code == 409
    log = client.get("/api/v1/devices/room/observation/log").json()
    assert log[0]["kind"] == "watched_change" and log[0]["spoken"] is True
    # Only watched tasks can be triggered.
    plain = look_job(client, question="使用者是不是駝背？")
    assert client.post(f"/api/v1/devices/room/schedules/{plain['id']}/trigger").status_code == 409


def test_a_one_off_change_task_compares_after_its_baseline(app):
    application, client = app
    eyes(application, changed("no", "箱子在右邊"), changed("no", "箱子還在"))
    response = client.post(
        "/api/v1/devices/room/schedules",
        json={
            "kind": "once",
            "minutes": 10,
            "purpose": "look",
            "message": "箱子",
            "look": {"mode": "change", "question": "箱子"},
        },
    )
    job_id = response.json()["id"]
    assert due(application) == 1
    capture(client, pending(client)["requests"][0]["task_id"])
    with application.state.session_factory() as session:
        job = session.get(ScheduledJobRow, job_id)
        assert job.status == "active" and job.runs == 1  # the comparison is still to come
    assert due(application, minutes=11) == 1
    assert capture(client, pending(client)["requests"][0]["task_id"]).json()["decision"] == "done"
    with application.state.session_factory() as session:
        assert session.get(ScheduledJobRow, job_id).status == "done"


def test_a_report_tells_the_scene_with_the_room(app):
    application, client = app
    eyes(application, seen(0, ["一隻貓（趴在箱子上）"], summary="一隻貓趴在箱子上"))
    client.post(
        "/api/v1/telemetry",
        json={
            "schema_version": 1,
            "message_id": "m-1",
            "device_id": "room",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence": 1,
            "metrics": {"temperature_c": 27, "humidity_pct": 60, "co2_ppm": 1300},
        },
    )
    look_job(client, mode="report", question="這邊的情況")
    assert due(application, minutes=31) == 1
    result = capture(client, pending(client)["requests"][0]["task_id"]).json()
    assert (result["result"], result["notice"]) == ("seen", "report")
    [text] = notices(application, client)
    assert text.startswith("我看了一下：一隻貓趴在箱子上。環境：") and "1300" in text


def test_check_tasks_record_but_stay_quiet_in_quiet_hours(app):
    application, client = app
    eyes(application, changed("yes", "背彎著"), changed("yes", "背彎著"))
    look_job(client, question="使用者是不是駝背？")
    client.put("/api/v1/devices/room/settings", json={"quiet_hours": "00:00-23:59"})
    assert due(application, minutes=31) == 1
    task = pending(client)["requests"][0]["task_id"]  # photos are still taken
    capture(client, task)
    assert capture(client, task, attempt=2).json()["decision"] == "muted"
    assert notices(application, client) == []
    log = client.get("/api/v1/devices/room/observation/log").json()
    assert log[0]["kind"] == "task" and log[0]["spoken"] is False
