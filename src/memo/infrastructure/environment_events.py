"""Transactional event projection, shared by HTTP and MQTT ingestion."""

from datetime import timedelta
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import func, select, update

from memo.domain.attention import AttentionPolicy
from memo.domain.comfort import feels_like_c
from memo.domain.environment import LEVEL_LABELS, RULES, advance
from memo.infrastructure.database import EventStateRow, ScheduledJobRow, TaskMemoryRow

# A notice stays deliverable this long; newer notices for the same rule supersede it sooner.
NOTICE_TTL = timedelta(minutes=10)
SCHEDULE_RULE = "schedule"


@lru_cache(maxsize=1)
def default_policy() -> AttentionPolicy:
    from memo.infrastructure.settings import AttentionSettings

    return AttentionSettings().policy()


def reading_metrics(reading) -> dict:
    return {
        "temperature_c": reading.temperature_c,
        "humidity_pct": reading.humidity_pct,
        "co2_ppm": reading.co2_ppm,
        "feels_like_c": feels_like_c(reading.temperature_c, reading.humidity_pct),
    }


def spoken_last_hour(session, device_id, at) -> int:
    """Environment notices queued for this device in the past hour (cancelled ones excluded)."""
    return session.scalar(
        select(func.count())
        .select_from(TaskMemoryRow)
        .where(
            TaskMemoryRow.device_id == device_id,
            TaskMemoryRow.rule_id != SCHEDULE_RULE,
            TaskMemoryRow.created_at >= at - timedelta(hours=1),
            TaskMemoryRow.status != "cancelled",
        )
    )


def project_reading(session, reading, policy: AttentionPolicy | None = None):
    policy = policy or default_policy()
    stamp = reading.observed_at.timestamp()
    age = (reading.received_at - reading.observed_at).total_seconds()
    # Historical/replayed/future samples remain history, never drive live events.
    if not 0 <= age <= 30:
        return
    metrics = reading_metrics(reading)
    for rule in RULES:
        row = session.scalar(
            select(EventStateRow)
            .where(EventStateRow.device_id == reading.device_id, EventStateRow.rule_id == rule.id)
            .with_for_update()
        )
        before = row.state if row else {}
        state, notice = advance(rule, before, stamp, metrics[rule.metric], reading.quality == "ok")
        if state == before:
            continue
        if notice or (state.get("status") == "unknown" and before.get("status") != "unknown"):
            state["version"] = before.get("version", 0) + 1
            # A newer notice supersedes undelivered older ones for the same rule.
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
        # The state still advances when policy keeps MEMO quiet, so silence is not retried.
        if notice and policy.allows(
            notice,
            state.get("level"),
            reading.received_at,
            spoken_last_hour(session, reading.device_id, reading.received_at),
        ):
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
                    expires_at=reading.received_at + NOTICE_TTL,
                    response=None,
                    lease_until=None,
                )
            )


def attention_context(rule_id: str, state: dict, notice: str | None = None) -> dict:
    """What the rule engine knows about a live situation, for the prompt and templates."""
    last = state.get("last") or 0
    level = state.get("level")
    return {
        "rule": rule_id,
        "notice": notice,
        "level": level,
        "level_label": LEVEL_LABELS.get(level),
        "value": state.get("score"),
        "minutes_active": int((last - state.get("active_since", last)) // 60),
        "reminders": state.get("reminders", 0),
        "snoozed": state.get("snoozed_until", 0) > last,
    }


def current_task(session, task, now):
    if task.rule_id == SCHEDULE_RULE:
        job = session.get(ScheduledJobRow, task.event_id)
        return task.expires_at > now and job is not None and job.status != "cancelled"
    row = session.get(EventStateRow, (task.device_id, task.rule_id))
    state = row.state if row else {}
    return (
        task.expires_at > now
        and state.get("event_id") == task.event_id
        and state.get("version") == task.event_version
        and state.get("status")
        in (
            ("resolved", "normal", "pending")
            if task.event_status == "resolved"
            else ("active", "recovering")
        )
        and 0 <= now.timestamp() - state.get("last", 0) <= 30
    )
