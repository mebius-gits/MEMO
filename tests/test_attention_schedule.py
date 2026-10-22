"""Severity, reminders, quiet hours, scheduled jobs and the welcome-back digest."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from memo.application.interaction import InteractionService
from memo.domain.attention import AttentionPolicy
from memo.domain.comfort import feels_like_c
from memo.domain.environment import REPEAT_SECONDS, RULES_BY_ID, advance
from memo.domain.interaction import EmotionLabel, Reply
from memo.domain.schedule import following_run, next_daily, parse_intent, validate_action
from memo.infrastructure.database import ScheduledJobRow, TaskMemoryRow
from memo.infrastructure.environment_events import project_reading
from memo.infrastructure.notifications import dispatch_one
from memo.infrastructure.schedules import fire_due
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.main import create_app

TZ = "Asia/Taipei"


def test_feels_like_follows_heat_index():
    assert feels_like_c(34, 60) >= 41  # hot and humid: danger territory
    assert feels_like_c(27.5, 68) < 30  # the top of Node-RED's normal band stays quiet
    assert feels_like_c(31, 20) == 31  # dry air never feels cooler than it is
    assert feels_like_c(26, None) == 26 and feels_like_c(None, 50) is None


def run(rule_id, values, step=5, start=0, state=None):
    rule, state, notices = RULES_BY_ID[rule_id], dict(state or {}), []
    for i, value in enumerate(values):
        state, notice = advance(rule, state, start + i * step, value, True)
        if notice:
            notices.append((start + i * step, notice, state.get("level")))
    return state, notices


def test_sustained_heat_reminds_by_severity_and_escalates():
    # 36 feels-like is "warning": active after the 60 s hold, then every 15 minutes.
    state, notices = run("temperature_high", [36] * 400)
    kinds = [kind for _, kind, _ in notices]
    assert kinds[0] == "active" and notices[0][2] == "warning"
    gaps = {b[0] - a[0] for a, b in zip(notices, notices[1:])}
    assert set(kinds[1:]) == {"reminder"} and gaps == {REPEAT_SECONDS["warning"]}
    assert state["reminders"] == len(kinds) - 1
    # Rising into danger escalates at once (after the anti-flap gap), then repeats every 5 min.
    state, more = run("temperature_high", [43] * 200, start=2000, state=state)
    assert more[0][1:] == ("escalated", "danger")
    assert {b[0] - a[0] for a, b in zip(more, more[1:])} == {REPEAT_SECONDS["danger"]}


def test_snooze_holds_reminders_but_not_escalation():
    state, _ = run("temperature_high", [36] * 20)
    state["snoozed_until"] = state["last"] + 3600
    state, notices = run("temperature_high", [36] * 300, start=state["last"] + 5, state=state)
    assert notices == []
    state, notices = run("temperature_high", [43] * 30, start=state["last"] + 5, state=state)
    assert notices[0][1:] == ("escalated", "danger")


def test_low_side_rules_and_resolution():
    _, notices = run("humidity_low", [22] * 40 + [45] * 40)
    assert [(kind, level) for _, kind, level in notices] == [
        ("active", "warning"),
        ("resolved", None),
    ]
    _, notices = run("temperature_low", [11] * 30)
    assert notices[0][1:] == ("active", "danger")


def test_policy_quiet_hours_cap_and_danger():
    policy = AttentionPolicy.from_settings("23:00-7:00", 2, TZ)
    night = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)  # 00:00 in Taipei
    day = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)  # 12:00 in Taipei
    assert policy.quiet(night) and not policy.quiet(day)
    assert not policy.allows("reminder", "warning", night, 0)
    assert policy.allows("reminder", "danger", night, 99)
    assert not policy.allows("resolved", "danger", night, 0)
    assert policy.allows("active", "caution", day, 1)
    assert not policy.allows("active", "caution", day, 2)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10 分鐘後提醒我喝水", {"kind": "once", "minutes": 10, "message": "喝水"}),
        ("半小時後提醒我休息一下", {"kind": "once", "minutes": 30}),
        ("提醒我十五分鐘後關火", {"kind": "once", "minutes": 15, "message": "關火"}),
        ("每小時提醒我起來走走", {"kind": "interval", "minutes": 60, "message": "起來走走"}),
        ("每天 9 點報環境摘要", {"kind": "daily", "time": "09:00", "purpose": "summary"}),
        ("每天下午3點半提醒我吃藥", {"kind": "daily", "time": "15:30", "message": "吃藥"}),
        ("我開窗了", {"kind": "followup", "minutes": 5, "message": "我開窗"}),
        ("我剛剛打開冷氣，10分鐘後跟我說", {"kind": "followup", "minutes": 10}),
        ("30分鐘後再提醒我", {"type": "snooze", "minutes": 30}),
        ("知道了", {"type": "snooze"}),
        ("不用再提醒我喝水了", {"type": "cancel", "match": "喝水"}),
        ("取消所有提醒", {"type": "cancel", "match": ""}),
        ("暫停提醒", {"type": "pause"}),
        ("恢復提醒", {"type": "resume"}),
        ("要不要開窗？", None),
        ("今天好熱喔", None),
    ],
)
def test_parse_intent(text, expected):
    action = parse_intent(text)
    if expected is None:
        assert action is None
    else:
        assert action is not None and expected.items() <= action.items()


def test_validate_action_bounds():
    ok = {"type": "schedule", "kind": "interval", "message": "喝水", "minutes": 30}
    assert validate_action(ok)["minutes"] == 30
    assert validate_action(ok | {"minutes": 2}) is None  # intervals under 5 minutes nag
    assert validate_action(ok | {"message": ""}) is None
    assert validate_action({"type": "schedule", "kind": "daily", "time": "25:00"}) is None
    assert validate_action({"type": "snooze", "minutes": 999}) is None
    assert validate_action({"type": "shell", "command": "rm"}) is None


def test_daily_and_interval_timing():
    late = datetime(2026, 10, 22, 14, 30, tzinfo=timezone.utc)  # 22:30 in Taipei
    assert next_daily("09:00", late, TZ) == datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
    due = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)
    # Missed interval runs are skipped, not replayed in a burst.
    assert following_run("interval", 60, None, due, due + timedelta(hours=3, minutes=1), TZ) == (
        due + timedelta(hours=4)
    )
    assert following_run("once", 10, None, due, due, TZ) is None


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'schedule.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    application = create_app(Settings(database_url=url, notifications_enabled=False))
    with TestClient(application) as client:
        client.post("/api/v1/devices", json={"id": "room", "name": "Room"})
        yield application, client


def telemetry(client, temperature, humidity=60, co2=800, sequence=1):
    return client.post(
        "/api/v1/telemetry",
        json={
            "schema_version": 1,
            "message_id": f"m-{sequence}",
            "device_id": "room",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "metrics": {"temperature_c": temperature, "humidity_pct": humidity, "co2_ppm": co2},
        },
    )


def chat(client, text, request_id):
    return client.post(
        "/api/v1/devices/room/interaction",
        json={"session_id": "s", "request_id": request_id, "user_message": text},
    ).json()


def test_chat_schedules_fire_and_deliver_through_notifications(app):
    application, client = app
    sessions = application.state.session_factory
    reply = chat(client, "10 分鐘後提醒我喝水", "00000000-0000-0000-0000-000000000001")
    assert reply["actions"][0].startswith("已設定：10 分鐘後")
    assert "喝水" in reply["text"]
    # Retrying the same request returns the saved reply and does not schedule twice.
    chat(client, "10 分鐘後提醒我喝水", "00000000-0000-0000-0000-000000000001")
    jobs = client.get("/api/v1/devices/room/schedules").json()
    assert len(jobs) == 1 and jobs[0]["kind"] == "once" and jobs[0]["message"] == "喝水"

    later = datetime.now(timezone.utc) + timedelta(minutes=11)
    assert fire_due(sessions, later, TZ) == 1
    assert fire_due(sessions, later, TZ) == 0  # a run fires exactly once
    assert dispatch_one(sessions, InteractionService(TemplateReplyGenerator()))
    notices = client.get("/api/v1/devices/room/notifications").json()
    assert notices[0]["event_status"] == "remind" and "喝水" in notices[0]["response"]["text"]
    assert client.get("/api/v1/devices/room/schedules").json() == []  # once jobs finish


def test_followup_reports_before_and_after(app):
    application, client = app
    sessions = application.state.session_factory
    telemetry(client, 26, co2=1400, sequence=1)
    chat(client, "我開窗了", "00000000-0000-0000-0000-000000000002")
    telemetry(client, 26, co2=900, sequence=2)
    assert fire_due(sessions, datetime.now(timezone.utc) + timedelta(minutes=6), TZ) == 1
    assert dispatch_one(sessions, InteractionService(TemplateReplyGenerator()))
    text = client.get("/api/v1/devices/room/notifications").json()[0]["response"]["text"]
    assert text.startswith("你開窗之後") and "CO₂從 1400 降到 900 ppm" in text


def test_schedule_api_pause_resume_cancel(app):
    _, client = app
    created = client.post(
        "/api/v1/devices/room/schedules",
        json={"kind": "daily", "purpose": "summary", "time": "9:00"},
    )
    assert created.status_code == 201 and created.json()["description"] == "每天 09:00 報環境摘要"
    job_id = created.json()["id"]
    paused = client.patch(f"/api/v1/devices/room/schedules/{job_id}", json={"status": "paused"})
    assert paused.json()["status"] == "paused"
    assert client.delete(f"/api/v1/devices/room/schedules/{job_id}").status_code == 204
    assert client.get("/api/v1/devices/room/schedules").json() == []
    bad = client.post("/api/v1/devices/room/schedules", json={"kind": "interval", "minutes": 1})
    assert bad.status_code == 422


def test_heat_notices_respect_quiet_hours_and_cap(app):
    application, _ = app
    quiet = AttentionPolicy.from_settings("0:00-23:59", 6, TZ)
    start = datetime.now(timezone.utc)

    def readings(temperature, count, offset=0):
        with application.state.session_factory() as session:
            for i in range(count):
                at = start + timedelta(seconds=(offset + i) * 5)
                project_reading(
                    session,
                    SimpleNamespace(
                        device_id="room",
                        observed_at=at,
                        received_at=at,
                        temperature_c=temperature,
                        humidity_pct=60,
                        co2_ppm=800,
                        quality="ok",
                    ),
                    quiet,
                )
            session.commit()
            return session.scalars(select(TaskMemoryRow).order_by(TaskMemoryRow.created_at)).all()

    assert readings(31, 20) == []  # caution stays silent during quiet hours
    tasks = readings(36, 40, offset=20)  # but escalating into danger always speaks
    assert [t.event_status for t in tasks] == ["escalated"]


def test_model_action_is_validated_before_running(app):
    application, client = app

    class ProposingGenerator:
        def __init__(self, action):
            self.action = action

        def generate(self, prepared):
            return Reply("好，幫你設定了。", EmotionLabel.HAPPY, 0.5, "model", None, self.action)

    application.state.interaction_service = InteractionService(
        ProposingGenerator({"type": "schedule", "kind": "once", "minutes": 20, "message": "收衣服"})
    )
    reply = chat(client, "等等記得叫我收衣服", "00000000-0000-0000-0000-000000000003")
    assert reply["actions"] and "收衣服" in reply["actions"][0]
    application.state.interaction_service = InteractionService(
        ProposingGenerator({"type": "schedule", "kind": "once", "minutes": 99999, "message": "x"})
    )
    reply = chat(client, "很久以後提醒我", "00000000-0000-0000-0000-000000000004")
    assert reply["actions"] == [] and "沒設定成功" in reply["text"]
    with application.state.session_factory() as session:
        assert len(session.scalars(select(ScheduledJobRow)).all()) == 1


def test_digest_summarises_missed_notices(app):
    application, client = app
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert client.post("/api/v1/devices/room/digest", json={"since": since.isoformat()}).json() == {
        "text": None,
        "emotion": None,
        "missed": 0,
        "source": None,
    }
    chat(client, "1 分鐘後提醒我喝水", "00000000-0000-0000-0000-000000000005")
    sessions = application.state.session_factory
    fire_due(sessions, datetime.now(timezone.utc) + timedelta(minutes=2), TZ)
    with sessions() as session:  # nobody was watching, so the notice expired
        session.scalars(select(TaskMemoryRow)).one().status = "expired"
        session.commit()
    digest = client.post("/api/v1/devices/room/digest", json={"since": since.isoformat()}).json()
    assert digest["missed"] == 1 and "1 則提醒" in digest["text"]
