"""Transactional event projection, shared by HTTP and MQTT ingestion."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from memo.domain.environment import RULES, advance
from memo.infrastructure.database import EventStateRow, TaskMemoryRow


def project_reading(session, reading):
    stamp = reading.observed_at.timestamp()
    age = (reading.received_at - reading.observed_at).total_seconds()
    # Historical/replayed/future samples remain history, never drive live events.
    if not 0 <= age <= 30:
        return
    for rule in RULES:
        row = session.scalar(
            select(EventStateRow)
            .where(EventStateRow.device_id == reading.device_id, EventStateRow.rule_id == rule.id)
            .with_for_update()
        )
        before = row.state if row else {}
        state, notice = advance(
            rule, before, stamp, getattr(reading, rule.metric), reading.quality == "ok"
        )
        if state == before:
            continue
        if notice or (state.get("status") == "unknown" and before.get("status") != "unknown"):
            state["version"] = before.get("version", 0) + 1
            session.execute(
                update(TaskMemoryRow)
                .where(
                    TaskMemoryRow.device_id == reading.device_id,
                    TaskMemoryRow.rule_id == rule.id,
                    TaskMemoryRow.status.in_(["queued", "generating", "ready"]),
                )
                .values(status="cancelled")
            )
        if row:
            row.state = state
        else:
            session.add(EventStateRow(device_id=reading.device_id, rule_id=rule.id, state=state))
        if notice:
            session.add(
                TaskMemoryRow(
                    id=uuid4().hex,
                    device_id=reading.device_id,
                    rule_id=rule.id,
                    event_id=state["event_id"],
                    event_version=state["version"],
                    event_status=notice,
                    status="queued",
                    created_at=reading.received_at,
                    expires_at=reading.received_at + timedelta(seconds=120),
                    response=None,
                    lease_until=None,
                )
            )


def current_task(session, task, now):
    row = session.get(EventStateRow, (task.device_id, task.rule_id))
    state = row.state if row else {}
    return (
        task.expires_at > now
        and state.get("event_id") == task.event_id
        and state.get("version") == task.event_version
        and state.get("status")
        in (
            ("active", "recovering")
            if task.event_status == "active"
            else ("resolved", "normal", "pending")
        )
        and 0 <= now.timestamp() - state.get("last", 0) <= 30
    )
