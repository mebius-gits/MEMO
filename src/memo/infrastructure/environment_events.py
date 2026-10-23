"""Transactional event projection, shared by HTTP and MQTT ingestion and the stale sweep."""

from dataclasses import replace
from datetime import timedelta
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import func, select, update

from memo.domain.attention import AttentionPolicy
from memo.domain.comfort import feels_like_c
from memo.domain.environment import (
    LEVEL_LABELS,
    RULES,
    SENSOR_RULE,
    Rule,
    advance,
    configured,
    connectivity,
    connectivity_sweep,
    expire,
)
from memo.infrastructure.database import (
    DeviceRow,
    DeviceSettingRow,
    EventStateRow,
    EventUpdateRow,
    RuleConfigRow,
    ScheduledJobRow,
    TaskMemoryRow,
)

# A notice stays deliverable this long; newer notices for the same rule supersede it sooner.
NOTICE_TTL = timedelta(minutes=10)
SCHEDULE_RULE = "schedule"
METRICS = ("temperature_c", "humidity_pct", "co2_ppm", "illuminance_lux")


@lru_cache(maxsize=1)
def default_policy() -> AttentionPolicy:
    from memo.infrastructure.settings import AttentionSettings

    return AttentionSettings().policy()


def policy_for(session, device_id) -> AttentionPolicy:
    """Server defaults with the device's own quiet hours and hourly cap, when set."""
    policy = default_policy()
    row = session.get(DeviceSettingRow, device_id)
    preferences = (row.preferences if row else None) or {}
    if preferences.get("quiet_hours") is not None:
        parsed = AttentionPolicy.from_settings(
            preferences["quiet_hours"], policy.max_per_hour, policy.timezone
        )
        policy = replace(policy, quiet_start=parsed.quiet_start, quiet_end=parsed.quiet_end)
    if preferences.get("max_proactive_per_hour") is not None:
        policy = replace(policy, max_per_hour=preferences["max_proactive_per_hour"])
    return policy


def rule_set(session) -> list[tuple[Rule, int]]:
    """Enabled rules with stored overrides applied, and the version each event will cite."""
    configs = {row.rule_id: row for row in session.scalars(select(RuleConfigRow))}
    rules = []
    for rule in RULES:
        config = configs.get(rule.id)
        if config is None:
            rules.append((rule, 1))
        elif config.enabled:
            rules.append((configured(rule, config.override), config.version))
    return rules


def reading_metrics(reading) -> dict:
    return {
        "temperature_c": reading.temperature_c,
        "humidity_pct": reading.humidity_pct,
        "co2_ppm": reading.co2_ppm,
        # Optional light sensor; readings from before it existed have none.
        "illuminance_lux": getattr(reading, "illuminance_lux", None),
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


def cancel_undelivered(session, device_id, rule_id):
    session.execute(
        update(TaskMemoryRow)
        .where(
            TaskMemoryRow.device_id == device_id,
            TaskMemoryRow.rule_id == rule_id,
            TaskMemoryRow.status.in_(["queued", "generating", "ready"]),
        )
        .values(status="cancelled")
    )


def log_update(session, device_id, rule_id, state, kind, at, detail=None):
    if not state.get("event_id"):
        return
    session.add(
        EventUpdateRow(
            device_id=device_id,
            rule_id=rule_id,
            event_id=state["event_id"],
            event_version=state.get("version", 0),
            kind=kind,
            level=state.get("level"),
            value=state.get("score"),
            detail=detail,
            at=at,
        )
    )


def locked_states(session, device_id, rule_id=None):
    query = select(EventStateRow).where(EventStateRow.device_id == device_id)
    if rule_id is not None:
        query = query.where(EventStateRow.rule_id == rule_id)
    return session.scalars(query.with_for_update()).all()


def record(session, row, device_id, rule_id, before, state, kind, notice, at, policy):
    """Persist a state change; a lifecycle change (kind) bumps the version, supersedes
    undelivered notices and is logged; a notice is queued when attention policy allows."""
    if state == before:
        return
    if kind:
        state["version"] = before.get("version", 0) + 1
        # A newer notice supersedes undelivered older ones for the same rule.
        cancel_undelivered(session, device_id, rule_id)
    if row:
        row.state = state
    else:
        session.add(EventStateRow(device_id=device_id, rule_id=rule_id, state=state))
    # The state still advances when policy keeps MEMO quiet, so silence is not retried.
    spoken = bool(notice) and policy.allows(
        notice, state.get("level"), at, spoken_last_hour(session, device_id, at)
    )
    if kind:
        detail = {"spoken": spoken} if notice else {}
        if kind == "active" and "rule_version" in state:
            detail["rule_version"] = state["rule_version"]
        log_update(session, device_id, rule_id, state, kind, at, detail or None)
    if spoken:
        session.add(
            TaskMemoryRow(
                id=uuid4().hex,
                device_id=device_id,
                rule_id=rule_id,
                event_id=state["event_id"],
                event_version=state["version"],
                event_status=notice,
                status="queued",
                created_at=at,
                expires_at=at + NOTICE_TTL,
                response=None,
                lease_until=None,
            )
        )


def sensor_kind(before: dict, state: dict) -> str | None:
    if state.get("live") and not before.get("live"):
        return "active"
    if before.get("live") and not state.get("live"):
        return "resolved"
    return None


def project_reading(session, reading, policy: AttentionPolicy | None = None):
    stamp = reading.observed_at.timestamp()
    age = (reading.received_at - reading.observed_at).total_seconds()
    # Historical/replayed/future samples remain history, never drive live events.
    if not 0 <= age <= 30:
        return
    device_id, at = reading.device_id, reading.received_at
    policy = policy or policy_for(session, device_id)
    metrics = reading_metrics(reading)
    valid = reading.quality == "ok"
    for rule, version in rule_set(session):
        rows = locked_states(session, device_id, rule.id)
        row = rows[0] if rows else None
        before = row.state if row else {}
        state, notice = advance(rule, before, stamp, metrics[rule.metric], valid)
        if notice == "active":
            state["rule_version"] = version
        unknown = state.get("status") == "unknown" and before.get("status") != "unknown"
        kind = notice or ("unknown" if unknown else None)
        record(session, row, device_id, rule.id, before, state, kind, notice, at, policy)
    rows = locked_states(session, device_id, SENSOR_RULE)
    row = rows[0] if rows else None
    before = row.state if row else {}
    usable = valid and any(metrics[key] is not None for key in METRICS)
    state, notice = connectivity(before, stamp, usable)
    kind = sensor_kind(before, state)
    record(session, row, device_id, SENSOR_RULE, before, state, kind, notice, at, policy)


def swept(rules: dict, rule_id: str, state: dict, now: float) -> tuple[dict, str | None, str]:
    """The state after a wall-clock staleness check, its notice and log kind."""
    if rule_id == SENSOR_RULE:
        after, notice = connectivity_sweep(state, now)
        return after, notice, sensor_kind(state, after)
    rule = rules.get(rule_id)
    if rule is None:
        return state, None, None
    after = expire(rule, state, now)
    unknown = after.get("status") == "unknown" and state.get("status") != "unknown"
    return after, None, "unknown" if unknown else None


def sweep_stale(sessions, now, policy: AttentionPolicy | None = None) -> int:
    """Detect silent sensors without waiting for a reading. Devices are locked like ingestion,
    and the check is repeated under the lock, so concurrent sweeps change a state once."""
    stamp = now.timestamp()
    changed = 0
    with sessions() as session:
        rules = {rule.id: rule for rule, _ in rule_set(session)}
        rows = session.scalars(select(EventStateRow)).all()
        devices = sorted(
            {
                row.device_id
                for row in rows
                if swept(rules, row.rule_id, row.state, stamp)[0] != row.state
            }
        )
        session.rollback()
        for device_id in devices:
            session.execute(
                update(DeviceRow).where(DeviceRow.id == device_id).values(name=DeviceRow.name)
            )
            device_policy = policy or policy_for(session, device_id)
            for row in locked_states(session, device_id):
                before = row.state
                state, notice, kind = swept(rules, row.rule_id, before, stamp)
                if state != before:
                    record(
                        session,
                        row,
                        device_id,
                        row.rule_id,
                        before,
                        state,
                        kind,
                        notice,
                        now,
                        device_policy,
                    )
                    changed += 1
            session.commit()
    return changed


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
    resolved = task.event_status == "resolved"
    same_event = (
        task.expires_at > now
        and state.get("event_id") == task.event_id
        and state.get("version") == task.event_version
    )
    if task.rule_id == SENSOR_RULE:
        # An offline notice is about missing data, so it cannot require fresh data.
        return same_event and state.get("status") in (
            ("resolved", "normal") if resolved else ("active", "recovering")
        )
    return (
        same_event
        and state.get("status")
        in (("resolved", "normal", "pending") if resolved else ("active", "recovering"))
        and 0 <= now.timestamp() - state.get("last", 0) <= 30
    )
