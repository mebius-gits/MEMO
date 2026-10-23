"""Event evidence and user actions on a live event: acknowledge, snooze, handled.

Acknowledging or snoozing changes only when MEMO repeats itself; it never changes whether
the environment is abnormal. "Handled" is the user's own statement, followed up with data.
"""

from datetime import datetime, timezone

from sqlalchemy import select, update

from memo.domain.entities import Conflict, NotFound
from memo.domain.environment import LEVELS, REPEAT_SECONDS, SENSOR_RULE, STALE_SECONDS
from memo.infrastructure.database import DeviceRow, EventStateRow, EventUpdateRow
from memo.infrastructure.environment_events import cancel_undelivered, locked_states, log_update
from memo.infrastructure.schedules import ScheduleRepository

ENDINGS = ("resolved", "rule_disabled")


def current_status(rule_id: str, state: dict, now: float) -> str:
    """Rule status as seen now: without fresh data a rule's status is unknown. The offline
    event is itself about missing data, so it keeps its own status."""
    if rule_id == SENSOR_RULE or 0 <= now - state.get("last", 0) <= STALE_SECONDS:
        return state.get("status", "normal")
    return "unknown"


def update_output(row: EventUpdateRow) -> dict:
    return {
        "kind": row.kind,
        "event_version": row.event_version,
        "level": row.level,
        "value": row.value,
        "detail": row.detail,
        "at": row.at,
    }


def summarize(updates: list[EventUpdateRow], states: dict, now: float) -> dict:
    """One event from its updates, oldest first; status comes from the live rule state."""
    first, last = updates[0], updates[-1]
    levels = [u.level for u in updates if u.level in LEVELS]
    ended = next((u.at for u in reversed(updates) if u.kind in ENDINGS), None)
    state = states.get(first.rule_id, {})
    if state.get("event_id") == first.event_id:
        status = current_status(first.rule_id, state, now)
    else:
        status = "resolved" if ended else "superseded"
    rule_version = next(
        (u.detail.get("rule_version") for u in updates if u.detail and "rule_version" in u.detail),
        None,
    )
    return {
        "event_id": first.event_id,
        "rule_id": first.rule_id,
        "status": status,
        "started_at": first.at,
        "ended_at": ended,
        "peak_level": max(levels, key=LEVELS.index) if levels else None,
        "rule_version": rule_version,
        "last_update": last.kind,
        "updated_at": last.at,
        "update_count": len(updates),
    }


class EventRepository:
    def __init__(self, session, tz: str = "Asia/Taipei"):
        self.session = session
        self.tz = tz

    def require_device(self, device_id):
        if self.session.get(DeviceRow, device_id) is None:
            raise NotFound("Device does not exist")

    def states(self, device_id) -> dict:
        rows = self.session.scalars(
            select(EventStateRow).where(EventStateRow.device_id == device_id)
        )
        return {row.rule_id: row.state for row in rows}

    def history(self, device_id, limit=20) -> list[dict]:
        """Recent events, newest first, each summarised from its update log."""
        self.require_device(device_id)
        rows = self.session.scalars(
            select(EventUpdateRow)
            .where(EventUpdateRow.device_id == device_id)
            .order_by(EventUpdateRow.id.desc())
            .limit(2000)
        ).all()
        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(row.event_id, []).append(row)
        states = self.states(device_id)
        now = datetime.now(timezone.utc).timestamp()
        return [
            summarize(list(reversed(updates)), states, now)
            for updates in list(grouped.values())[:limit]
        ]

    def detail(self, device_id, event_id) -> dict:
        self.require_device(device_id)
        updates = self.session.scalars(
            select(EventUpdateRow)
            .where(EventUpdateRow.device_id == device_id, EventUpdateRow.event_id == event_id)
            .order_by(EventUpdateRow.id)
        ).all()
        if not updates:
            raise NotFound("Event not found")
        now = datetime.now(timezone.utc).timestamp()
        return summarize(updates, self.states(device_id), now) | {
            "updates": [update_output(row) for row in updates]
        }

    def live_row(self, device_id, event_id) -> EventStateRow:
        """The rule state carrying this event, locked like ingestion; it must still be live."""
        self.require_device(device_id)
        self.session.execute(
            update(DeviceRow).where(DeviceRow.id == device_id).values(name=DeviceRow.name)
        )
        for row in locked_states(self.session, device_id):
            if row.state.get("event_id") == event_id:
                if not row.state.get("live"):
                    raise Conflict("Event has already ended")
                return row
        raise NotFound("Event not found")

    def acknowledge(self, device_id, event_id) -> dict:
        """The user has seen it: undelivered notices are dropped and same-level reminders
        wait one repeat interval. A rise in severity still speaks up."""
        row = self.live_row(device_id, event_id)
        now = datetime.now(timezone.utc)
        state = dict(row.state)
        level = state.get("level")
        until = now.timestamp() + REPEAT_SECONDS[level] if level in REPEAT_SECONDS else 0
        state.update(
            acknowledged_at=now.timestamp(),
            snoozed_until=max(state.get("snoozed_until", 0), until),
        )
        row.state = state
        cancel_undelivered(self.session, device_id, row.rule_id)
        log_update(self.session, device_id, row.rule_id, state, "acknowledged", now)
        self.session.commit()
        return self.detail(device_id, event_id)

    def snooze(self, device_id, event_id, minutes: int) -> dict:
        row = self.live_row(device_id, event_id)
        now = datetime.now(timezone.utc)
        state = row.state | {"snoozed_until": now.timestamp() + minutes * 60}
        row.state = state
        log_update(
            self.session,
            device_id,
            row.rule_id,
            state,
            "snoozed",
            now,
            {"minutes": minutes, "via": "api"},
        )
        self.session.commit()
        return self.detail(device_id, event_id)

    def handled(self, device_id, event_id, action: dict):
        """Record what the user says they did and schedule a data-based follow-up."""
        row = self.live_row(device_id, event_id)
        now = datetime.now(timezone.utc)
        job = ScheduleRepository(self.session, self.tz).create(device_id, action, now)
        log_update(
            self.session,
            device_id,
            row.rule_id,
            row.state,
            "handled",
            now,
            {
                "statement": action["message"],
                "job_id": job.id,
                "followup_minutes": action["minutes"],
            },
        )
        self.session.commit()
        return job
