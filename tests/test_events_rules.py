from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from memo.domain.attention import AttentionPolicy
from memo.domain.entities import Device, Reading
from memo.domain.environment import (
    RULES_BY_ID,
    SENSOR_RULE,
    configured,
    connectivity,
    connectivity_sweep,
    expire,
    rule_problem,
)
from memo.infrastructure.database import (
    Base,
    EventStateRow,
    EventUpdateRow,
    TaskMemoryRow,
    build_engine,
)
from memo.infrastructure.environment_events import sweep_stale
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.settings import Settings
from memo.main import create_app

CO2 = RULES_BY_ID["co2_high"]
OPEN = AttentionPolicy()


def test_expire_breaks_holds_and_turns_live_events_unknown():
    assert expire(CO2, {"status": "pending", "since": 0, "last": 0}, 20)["status"] == "pending"
    pending = expire(CO2, {"status": "pending", "since": 0, "last": 0}, 31)
    assert pending["status"] == "normal" and pending["since"] is None
    live = {"status": "recovering", "live": True, "since": 5, "last": 0}
    assert expire(CO2, live, 31)["status"] == "unknown"
    # Already settled states are left alone, so repeated sweeps write nothing.
    settled = {"status": "normal", "since": None, "samples": [], "last": 0}
    assert expire(CO2, settled, 100) == settled


def test_sensor_offline_needs_three_valid_readings_to_recover():
    state, notice = connectivity({}, 0, True)
    assert state["status"] == "normal" and notice is None
    state, notice = connectivity_sweep(state, 31)
    assert state["status"] == "active" and state["live"] and notice == "active"
    assert connectivity_sweep(state, 60) == (state, None)
    state, _ = connectivity(state, 100, True)
    state, _ = connectivity(state, 105, True)
    assert state["status"] == "recovering"
    # An invalid reading restarts the count.
    state, _ = connectivity(state, 110, False)
    assert state["status"] == "active" and state["valid_streak"] == 0
    notices = []
    for t in (115, 120, 125):
        state, notice = connectivity(state, t, True)
        notices.append(notice)
    assert notices == [None, None, "resolved"] and not state["live"]


def test_outage_found_long_after_the_fact_is_not_announced():
    state, _ = connectivity({}, 0, True)
    state, notice = connectivity_sweep(state, 3600)
    assert state["live"] and notice is None
    for t in (3700, 3705, 3710):
        state, notice = connectivity(state, t, True)
    assert not state["live"] and notice is None


def test_rule_overrides_are_validated():
    assert rule_problem(CO2) is None
    assert rule_problem(configured(CO2, {"recovery": 1300})) is not None
    below = RULES_BY_ID["humidity_low"]
    assert rule_problem(configured(below, {"recovery": 30})) is not None
    unordered = {"levels": [[1500, "warning"], [1200, "caution"]]}
    assert rule_problem(configured(CO2, unordered)) is not None
    shuffled = {"levels": [[1200, "caution"], [1100, "warning"]]}
    assert rule_problem(configured(CO2, shuffled)) is not None
    trend = RULES_BY_ID["co2_rising"]
    assert rule_problem(configured(trend, {"levels": [[300, "caution"]]})) is not None
    assert configured(CO2, {"trigger": 900, "recovery": 800}).trigger == 900


def reading(device, at, co2=1400, quality="ok"):
    return Reading(
        message_id=uuid4().hex,
        device_id=device,
        observed_at=at,
        received_at=at,
        sequence=0,
        temperature_c=25,
        humidity_pct=50,
        co2_ppm=co2,
        quality=quality,
    )


@pytest.fixture
def sessions(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'sweep.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_sweep_detects_silence_without_new_readings(sessions):
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    with sessions() as session:
        repo = SqlAlchemyRepository(session)
        repo.add_device(Device("room", "Room", ""))
        for i in range(30):
            repo.add_reading(reading("room", start + timedelta(seconds=5 * i)))
        state = session.get(EventStateRow, ("room", "co2_high")).state
        assert state["status"] == "active"
        version = state["version"]
    last = start + timedelta(seconds=145)
    assert sweep_stale(sessions, last + timedelta(seconds=20), OPEN) == 0
    assert sweep_stale(sessions, last + timedelta(seconds=40), OPEN) > 0
    # A second sweep finds nothing left to change.
    assert sweep_stale(sessions, last + timedelta(seconds=41), OPEN) == 0
    with sessions() as session:
        state = session.get(EventStateRow, ("room", "co2_high")).state
        assert state["status"] == "unknown" and state["live"]
        assert state["version"] == version + 1
        sensor = session.get(EventStateRow, ("room", SENSOR_RULE)).state
        assert sensor["status"] == "active"
        tasks = session.scalars(select(TaskMemoryRow).order_by(TaskMemoryRow.created_at)).all()
        assert tasks[0].rule_id == "co2_high" and tasks[0].status == "cancelled"
        assert tasks[-1].rule_id == SENSOR_RULE and tasks[-1].event_status == "active"
        kinds = session.scalars(select(EventUpdateRow.kind).order_by(EventUpdateRow.id)).all()
        assert kinds == ["active", "unknown", "active"]
        # The page delivered the outage notice, so the recovery is worth saying.
        tasks[-1].status = "acknowledged"
        session.commit()
        repo = SqlAlchemyRepository(session)
        back = last + timedelta(seconds=60)
        for i in range(3):
            repo.add_reading(reading("room", back + timedelta(seconds=5 * i)))
        sensor = session.get(EventStateRow, ("room", SENSOR_RULE)).state
        assert sensor["status"] == "resolved"
        notice = session.scalars(
            select(TaskMemoryRow).where(TaskMemoryRow.event_status == "resolved")
        ).one()
        assert notice.rule_id == SENSOR_RULE


def test_invalid_quality_does_not_count_as_data(sessions):
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    with sessions() as session:
        repo = SqlAlchemyRepository(session)
        repo.add_device(Device("room", "Room", ""))
        repo.add_reading(reading("room", start))
        for i in range(1, 10):
            repo.add_reading(reading("room", start + timedelta(seconds=5 * i), quality="invalid"))
    assert sweep_stale(sessions, start + timedelta(seconds=50), OPEN) > 0
    with sessions() as session:
        assert session.get(EventStateRow, ("room", SENSOR_RULE)).state["live"]


# ---- HTTP -------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(Settings(database_url=url, _env_file=None))) as client:
        yield client


def register(client, device="room01"):
    client.post("/api/v1/devices", json={"id": device, "name": "Room"})


def telemetry(client, device="room01", at=None, **metrics):
    at = at or datetime.now(timezone.utc)
    return client.post(
        "/api/v1/telemetry",
        json={
            "message_id": uuid4().hex,
            "device_id": device,
            "sequence": 1,
            "observed_at": at.isoformat(),
            "metrics": metrics or {"temperature_c": 25, "humidity_pct": 50, "co2_ppm": 600},
        },
    )


def test_each_metric_is_validated_on_its_own(client):
    register(client)
    response = telemetry(client, temperature_c=25, humidity_pct=150, co2_ppm=700)
    assert response.status_code == 200
    assert response.json()["rejected_metrics"] == ["humidity_pct"]
    assert response.json()["humidity_pct"] is None
    assert response.json()["temperature_c"] == 25
    assert telemetry(client, humidity_pct=150).status_code == 422


def test_latest_state_and_summary(client):
    register(client)
    now = datetime.now(timezone.utc)
    telemetry(client, at=now - timedelta(seconds=10), temperature_c=24, co2_ppm=800)
    telemetry(client, at=now - timedelta(seconds=5), co2_ppm=900)
    state = client.get("/api/v1/devices/room01/state").json()
    assert state["online"]
    assert state["metrics"]["co2_ppm"]["value"] == 900
    assert state["metrics"]["temperature_c"]["value"] == 24
    assert state["metrics"]["humidity_pct"] is None
    assert state["feels_like_c"] == 24
    buckets = client.get(
        "/api/v1/devices/room01/readings/summary", params={"bucket_seconds": 3600}
    ).json()
    assert sum(b["count"] for b in buckets) == 2
    co2 = [b["co2_ppm"] for b in buckets if b["co2_ppm"]]
    assert {s["min"] for s in co2} | {s["max"] for s in co2} <= {800, 900}
    too_many = {"bucket_seconds": 10, "start": (now - timedelta(days=3)).isoformat()}
    assert client.get("/api/v1/devices/room01/readings/summary", params=too_many).status_code == 422
    assert client.get("/api/v1/devices/missing/state").status_code == 404


def instant_co2_rule(client, **changes):
    rule = client.get("/api/v1/rules/co2_high").json()
    body = rule["defaults"] | {"hold_seconds": 0, "enabled": True} | changes
    return client.put("/api/v1/rules/co2_high", json=body)


def test_rules_can_be_listed_validated_and_changed(client):
    rules = client.get("/api/v1/rules").json()
    assert len(rules) == 7 and all(rule["version"] == 1 for rule in rules)
    assert client.get("/api/v1/rules/nope").status_code == 404
    bad = instant_co2_rule(client, recovery=1300)
    assert bad.status_code == 422 and "恢復門檻" in bad.json()["detail"]
    changed = instant_co2_rule(client)
    assert changed.status_code == 200
    assert changed.json()["version"] == 2 and changed.json()["customized"]
    assert changed.json()["hold_seconds"] == 0
    # The very next reading uses the new hold: one high reading opens the event.
    register(client)
    telemetry(client, co2_ppm=1400)
    events = {e["rule_id"]: e for e in client.get("/api/v1/devices/room01/events").json()}
    assert events["co2_high"]["status"] == "active"
    assert events["co2_high"]["rule_version"] == 2
    # Disabling ends the live event without a notice.
    disabled = instant_co2_rule(client, enabled=False)
    assert disabled.json()["enabled"] is False
    events = {e["rule_id"]: e for e in client.get("/api/v1/devices/room01/events").json()}
    assert events["co2_high"]["status"] == "normal"
    history = client.get("/api/v1/devices/room01/events/history").json()
    co2 = next(e for e in history if e["rule_id"] == "co2_high")
    assert co2["last_update"] == "rule_disabled" and co2["ended_at"]


def test_event_history_and_actions(client):
    instant_co2_rule(client)
    register(client)
    telemetry(client, co2_ppm=1600)
    history = client.get("/api/v1/devices/room01/events/history").json()
    event = next(e for e in history if e["rule_id"] == "co2_high")
    assert event["status"] == "active" and event["peak_level"] == "warning"
    assert event["rule_version"] == 2
    base = f"/api/v1/devices/room01/events/{event['event_id']}"

    acked = client.post(base + "/ack").json()
    assert acked["last_update"] == "acknowledged"
    rules = {e["rule_id"]: e for e in client.get("/api/v1/devices/room01/events").json()}
    assert rules["co2_high"]["snoozed_until"] > datetime.now(timezone.utc).timestamp() + 800
    tasks = client.get("/api/v1/devices/room01/memory/tasks").json()
    assert all(t["status"] == "cancelled" for t in tasks if t["rule_id"] == "co2_high")

    assert client.post(base + "/snooze", json={"minutes": 1}).status_code == 422
    snoozed = client.post(base + "/snooze", json={"minutes": 30}).json()
    assert snoozed["updates"][-1]["detail"] == {"minutes": 30, "via": "api"}

    handled = client.post(base + "/handled", json={"message": "我開窗了", "minutes": 10})
    assert handled.status_code == 201
    assert handled.json()["followup"]["kind"] == "followup"
    assert handled.json()["event"]["updates"][-1]["detail"]["statement"] == "我開窗了"
    schedules = client.get("/api/v1/devices/room01/schedules").json()
    assert [job["kind"] for job in schedules] == ["followup"]

    detail = client.get(base).json()
    assert [u["kind"] for u in detail["updates"]] == [
        "active",
        "acknowledged",
        "snoozed",
        "handled",
    ]
    assert client.get("/api/v1/devices/room01/events/nope").status_code == 404
    assert client.post("/api/v1/devices/room01/events/nope/ack").status_code == 404


def test_actions_on_an_ended_event_conflict(client):
    instant_co2_rule(client)
    register(client)
    telemetry(client, co2_ppm=1400)
    event_id = client.get("/api/v1/devices/room01/events/history").json()[0]["event_id"]
    instant_co2_rule(client, enabled=False)
    assert client.post(f"/api/v1/devices/room01/events/{event_id}/ack").status_code == 409


def test_device_settings_override_server_defaults(client):
    register(client)
    settings = client.get("/api/v1/devices/room01/settings").json()
    assert settings["quiet_hours"] is None and settings["effective"]["max_proactive_per_hour"] == 6
    body = {
        "personality": {"tone": "lively", "persona": "愛吐槽的損友"},
        "quiet_hours": "23:00-07:00",
        "max_proactive_per_hour": 2,
    }
    saved = client.put("/api/v1/devices/room01/settings", json=body).json()
    assert saved["effective"]["quiet_hours"] == "23:00-07:00"
    assert saved["effective"]["max_proactive_per_hour"] == 2
    assert saved["personality"]["tone"] == "lively"
    assert client.get("/api/v1/devices/room01/settings").json() == saved
    bad = body | {"quiet_hours": "25:00-07:00"}
    assert client.put("/api/v1/devices/room01/settings", json=bad).status_code == 422
    assert client.put("/api/v1/devices/missing/settings", json=body).status_code == 404


def test_device_policy_limits_proactive_notices(sessions):
    from memo.infrastructure.database import DeviceSettingRow

    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    # Quiet hours around the readings, whatever the time of day (a fixed 00:00-23:59 window
    # left out the minute before midnight).
    local = start.astimezone(ZoneInfo("Asia/Taipei"))
    quiet = f"{local - timedelta(hours=1):%H:%M}-{local + timedelta(hours=1):%H:%M}"
    with sessions() as session:
        repo = SqlAlchemyRepository(session)
        repo.add_device(Device("room", "Room", ""))
        session.add(
            DeviceSettingRow(
                device_id="room",
                personality={},
                preferences={"quiet_hours": quiet, "max_proactive_per_hour": None},
                updated_at=start,
            )
        )
        session.commit()
        for i in range(30):
            repo.add_reading(reading("room", start + timedelta(seconds=5 * i)))
        state = session.get(EventStateRow, ("room", "co2_high")).state
        assert state["status"] == "active"
        # Caution-level CO2 inside the device's quiet hours stays silent but is logged.
        assert session.scalars(select(TaskMemoryRow)).all() == []
        update = session.scalars(select(EventUpdateRow)).one()
        assert update.detail == {"spoken": False, "rule_version": 1}


def test_trend_rule_override_keeps_window():
    trend = configured(RULES_BY_ID["co2_rising"], {"trigger": 200, "recovery": 50})
    assert trend.window == 60 and trend.smooth == 10
    assert replace(trend, trigger=300, recovery=100) == RULES_BY_ID["co2_rising"]
