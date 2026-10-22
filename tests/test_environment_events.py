from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from memo.application.interaction import InteractionService
from memo.domain.entities import Device, Reading
from memo.domain.environment import RULES, advance
from memo.infrastructure.database import Base, EventStateRow, TaskMemoryRow, build_engine
from memo.infrastructure.notifications import dispatch_one
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.workers.simulator import scenario_co2


def test_hold_hysteresis_gap_and_recovery():
    rule = RULES[0]
    state = {}
    notices = []
    for t in range(0, 125, 5):
        state, notice = advance(rule, state, t, 1400, True)
        if notice:
            notices.append(notice)
    assert notices == ["active"]
    state, notice = advance(rule, state, 125, 1100, True)
    assert state["status"] == "active" and notice is None
    state, _ = advance(rule, state, 130, 800, True)
    assert state["status"] == "recovering"
    state, _ = advance(rule, state, 150, 800, True)
    assert state["status"] == "recovering" and state["since"] == 150
    for t in range(155, 275, 5):
        state, notice = advance(rule, state, t, 800, True)
    assert state["status"] == "resolved" and notice == "resolved"


def test_invalid_missing_and_old_samples_never_resolve():
    rule = replace(RULES[0], hold=0)
    state, notice = advance(rule, {}, 0, 1400, True)
    assert notice == "active"
    for t, value, valid in [(5, None, True), (10, 800, False)]:
        state, notice = advance(rule, state, t, value, valid)
        assert state["status"] == "unknown" and notice is None
    assert advance(rule, state, 5, 800, True) == (state, None)


def test_trend_needs_covered_window_and_resets_after_gap():
    rule = RULES[3]
    state, _ = advance(rule, {}, 0, 800, True)
    state, notice = advance(rule, state, 5, 1500, True)
    assert notice is None
    for t in range(10, 65, 5):
        state, notice = advance(rule, state, t, 800 + 10 * t, True)
    assert state["live"]
    state, notice = advance(rule, state, 90, 1800, True)
    assert state["status"] == "unknown" and notice is None


def test_full_scenario_transitions():
    for rule in (RULES[0], RULES[3]):
        state, notices = {}, []
        for t in range(0, 365, 5):
            state, notice = advance(rule, state, t, scenario_co2(t), True)
            if notice:
                notices.append(notice)
        assert notices == ["active", "resolved"]


def test_high_frequency_trend_keeps_window_coverage():
    state, notices = {}, []
    for t in range(320):
        state, notice = advance(RULES[4], state, t, 25 + t / 100, True)
        if notice:
            notices.append(notice)
    assert notices == ["active"]
    assert len(state["samples"]) <= 128


def test_ingestion_atomic_dedup_dispatch_and_stale_reply(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'events.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    with sessions() as session:
        repo = SqlAlchemyRepository(session)
        repo.add_device(Device("room", "Room", ""))
        for i in range(25):
            at = now - timedelta(seconds=120 - i * 5)
            reading = Reading(
                message_id=str(i),
                device_id="room",
                observed_at=at,
                received_at=at,
                sequence=i,
                temperature_c=26,
                humidity_pct=60,
                co2_ppm=1400,
                quality="ok",
            )
            repo.add_reading(reading)
        repo.add_reading(reading)
        tasks = session.scalars(select(TaskMemoryRow)).all()
        assert len(tasks) == 1 and tasks[0].status == "queued"
        task_id = tasks[0].id
    assert dispatch_one(sessions, InteractionService(TemplateReplyGenerator()))
    with sessions() as session:
        task = session.get(TaskMemoryRow, task_id)
        assert task.status == "ready" and task.response["emotion"]["label"] == "concerned"
        assert "1400" not in task.response["text"]
        task.status = "queued"
        session.commit()

    class ChangingGenerator:
        def generate(self, prepared):
            with sessions() as session:
                row = session.get(EventStateRow, ("room", "co2_high"))
                row.state = row.state | {"version": row.state["version"] + 1, "status": "resolved"}
                session.commit()
            return TemplateReplyGenerator().generate(prepared)

    assert dispatch_one(sessions, InteractionService(ChangingGenerator()))
    with sessions() as session:
        assert session.get(TaskMemoryRow, task_id).status == "cancelled"
    engine.dispose()
