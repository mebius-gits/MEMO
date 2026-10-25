"""MEMO's own observation at run time: looking at the space, the situation, the log, notices.

While the camera is on, the web page sends a photo when the scene changes and settles, when
someone appears or leaves, and on a regular patrol. Each photo is described by the vision
model and recorded like a look task's photo (job "observe"); the description updates the
situation, differences become events in the observation log, and the few worth saying
(someone back after a while, an animal) are queued as notices for the dispatcher. Photos
are never stored.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from memo.domain.entities import Conflict
from memo.domain.environment import LEVEL_LABELS, RECOVERED, SENSOR_RULE, SUBJECTS
from memo.domain.observation import (
    EVENT_WORDS,
    LOG_DAYS,
    MIN_GAP_SECONDS,
    PATROL_MINUTES,
    changes,
    remember,
    situation,
    worth_saying,
)
from memo.domain.vision import clean_quality, quality_flags, scene_text
from memo.infrastructure.database import (
    EventUpdateRow,
    ObservationEventRow,
    ObservationStateRow,
    TaskMemoryRow,
    VisionObservationRow,
)
from memo.infrastructure.environment_events import NOTICE_TTL, OBSERVE_RULE, policy_for
from memo.infrastructure.vision_model import SPACE, VisionFailure

OBSERVE = OBSERVE_RULE  # task_memory.rule_id of observation notices; job_id of MEMO's photos
SKIPS = ("camera_off", "camera_unavailable", "permission_denied")
CLOCK_SLACK = timedelta(seconds=180)
# Someone appearing or leaving is looked at sooner than a scene change or a patrol.
PERSON_GAP_SECONDS = 5


def local_clock(at: datetime, tz: str) -> str:
    return at.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def brief_state(state: dict | None, tz: str) -> dict | None:
    """The situation as MEMO's prompts see it."""
    if not state:
        return None
    at = datetime.fromisoformat(state["at"])
    return {
        "seen_at": local_clock(at, tz),
        "people": state.get("people"),
        "people_detail": state.get("people_detail") or None,
        "animals": state.get("animals") or [],
        "lighting": state.get("lighting") or None,
        "summary": state.get("summary") or None,
    }


def log_line(row: ObservationEventRow, tz: str) -> dict:
    return {
        "at": local_clock(row.at, tz),
        "event": EVENT_WORDS.get(row.kind, row.kind),
        "detail": row.detail or None,
        "spoken": row.spoken,
    }


def situation_context(session, device_id, tz, now) -> dict:
    """What MEMO knows about the space now and today, for answering about it."""
    row = session.get(ObservationStateRow, device_id)
    start = datetime.now(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    events = session.scalars(
        select(ObservationEventRow)
        .where(ObservationEventRow.device_id == device_id, ObservationEventRow.at >= start)
        .order_by(ObservationEventRow.at.desc())
        .limit(12)
    ).all()
    counts = {}
    for kind, count in session.execute(
        select(ObservationEventRow.kind, func.count())
        .where(ObservationEventRow.device_id == device_id, ObservationEventRow.at >= start)
        .group_by(ObservationEventRow.kind)
    ):
        counts[EVENT_WORDS.get(kind, kind)] = count
    return {
        "now": brief_state(row.state if row else None, tz),
        "today_log": [log_line(event, tz) for event in reversed(events)],
        "today_counts": counts,
    }


def timeline(session, device_id, tz, since: datetime, limit: int = 100) -> list[dict]:
    """The observation log merged with the room's events, newest first."""
    entries = [
        {
            "at": row.at,
            "source": "look" if row.kind in ("watched_change", "task") else "observe",
            "kind": row.kind,
            "text": EVENT_WORDS.get(row.kind, row.kind) + (f"：{row.detail}" if row.detail else ""),
            "spoken": row.spoken,
        }
        for row in session.scalars(
            select(ObservationEventRow)
            .where(ObservationEventRow.device_id == device_id, ObservationEventRow.at >= since)
            .order_by(ObservationEventRow.at.desc())
            .limit(limit)
        )
    ]
    for row in session.scalars(
        select(EventUpdateRow)
        .where(
            EventUpdateRow.device_id == device_id,
            EventUpdateRow.at >= since,
            EventUpdateRow.kind.in_(["active", "escalated", "resolved"]),
        )
        .order_by(EventUpdateRow.at.desc())
        .limit(limit)
    ):
        if row.kind == "resolved":
            text = RECOVERED.get(row.rule_id, SUBJECTS.get(row.rule_id, row.rule_id))
        else:
            text = SUBJECTS.get(row.rule_id, row.rule_id)
            if row.level in LEVEL_LABELS and row.rule_id != SENSOR_RULE:
                text += f"（{LEVEL_LABELS[row.level]}）"
        entries.append(
            {
                "at": row.at,
                "source": "environment",
                "kind": row.kind,
                "text": text,
                "spoken": bool((row.detail or {}).get("spoken")),
            }
        )
    entries.sort(key=lambda entry: entry["at"], reverse=True)
    return entries[:limit]


def log_event(session, device_id, at, kind, detail="", spoken=False, data=None):
    row = ObservationEventRow(
        id=uuid4().hex,
        device_id=device_id,
        at=at,
        kind=kind,
        detail=detail or "",
        spoken=spoken,
        data=data,
    )
    session.add(row)
    return row


def notices_last_hour(session, device_id, now) -> int:
    return session.scalar(
        select(func.count())
        .select_from(TaskMemoryRow)
        .where(
            TaskMemoryRow.device_id == device_id,
            TaskMemoryRow.rule_id == OBSERVE,
            TaskMemoryRow.status != "cancelled",
            TaskMemoryRow.created_at >= now - timedelta(hours=1),
        )
    )


def prune(session, now):
    session.execute(
        delete(ObservationEventRow).where(ObservationEventRow.at < now - timedelta(days=LOG_DAYS))
    )


def forget_device(session, device_id):
    session.execute(delete(ObservationEventRow).where(ObservationEventRow.device_id == device_id))
    session.execute(delete(ObservationStateRow).where(ObservationStateRow.device_id == device_id))
    session.execute(
        delete(VisionObservationRow).where(
            VisionObservationRow.device_id == device_id, VisionObservationRow.job_id == OBSERVE
        )
    )


def notice_context(session, task, tz) -> dict:
    """Facts for an observation notice: the event, the situation, what happened meanwhile."""
    event = session.get(ObservationEventRow, task.event_id)
    state = session.get(ObservationStateRow, task.device_id)
    data = (event.data if event else None) or {}
    context = {
        "purpose": OBSERVE,
        "notice": task.event_status,
        "detail": event.detail if event else None,
        "now": brief_state(state.state if state else None, tz),
        "others": data.get("others", []),
    }
    if task.event_status == "arrival" and data.get("away_since"):
        since = datetime.fromisoformat(data["away_since"])
        context["away_minutes"] = round((event.at - since).total_seconds() / 60)
        meanwhile = session.scalars(
            select(ObservationEventRow)
            .where(
                ObservationEventRow.device_id == task.device_id,
                ObservationEventRow.at > since,
                ObservationEventRow.at < event.at,
                ObservationEventRow.kind.not_in(["start", "departure", "lights"]),
            )
            .order_by(ObservationEventRow.at)
            .limit(5)
        ).all()
        context["while_away"] = [log_line(row, tz) for row in meanwhile]
    return context


class ObservationService:
    """Receives the page's photo of the whole space and updates what MEMO knows about it."""

    def __init__(self, sessions, analyzer, tz: str = "Asia/Taipei"):
        self.sessions = sessions
        self.analyzer = analyzer
        self.tz = tz

    def config(self) -> dict:
        return {"patrol_minutes": PATROL_MINUTES, "min_gap_seconds": MIN_GAP_SECONDS}

    def observe(
        self,
        device_id: str,
        trigger: str,
        *,
        image: str | None = None,
        quality: dict | None = None,
        captured_at: datetime | None = None,
        skip: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        if captured_at is None or not now - CLOCK_SLACK <= captured_at <= now + timedelta(
            seconds=5
        ):
            captured_at = now
        gap = PERSON_GAP_SECONDS if trigger.startswith("person") else MIN_GAP_SECONDS - 2
        with self.sessions() as session:
            last = session.scalar(
                select(func.max(VisionObservationRow.created_at)).where(
                    VisionObservationRow.device_id == device_id,
                    VisionObservationRow.job_id == OBSERVE,
                )
            )
        if last is not None and (now - last).total_seconds() < gap:
            raise Conflict("Observed a moment ago")
        metrics = clean_quality(quality)
        flags = quality_flags(metrics)
        observation = reason = None
        if skip or not image:
            reason = skip if skip in SKIPS else "camera_unavailable"
        else:
            try:
                observation = self.analyzer.analyze(
                    device_id,
                    SPACE,
                    image,
                    captured_label=captured_at.astimezone(ZoneInfo(self.tz)).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                )
            except VisionFailure as failure:
                reason = failure.reason
        return self.settle(device_id, trigger, captured_at, observation, reason, metrics, flags)

    def settle(self, device_id, trigger, at, observation, reason, metrics, flags) -> dict:
        now = datetime.now(timezone.utc)
        quality = {"metrics": metrics, "flags": flags}
        if observation and observation.get("visibility"):
            quality["visibility"] = observation["visibility"]
        result = "skipped" if reason in SKIPS else (observation or {}).get("result", "unknown")
        with self.sessions() as session:
            record = VisionObservationRow(
                id=uuid4().hex,
                device_id=device_id,
                job_id=OBSERVE,
                task_id=None,
                run=0,
                attempt=1,
                captured_at=at,
                mode="report",
                question=SPACE["question"],
                result=result,
                detail=(observation or {}).get("detail"),
                scene=(observation or {}).get("scene"),
                quality=quality,
                reason=reason,
                decision=trigger,
                created_at=now,
            )
            session.add(record)
            response = {
                "status": "recorded",
                "result": result,
                "summary": record.detail,
                "reason": reason,
                "quality_flags": flags,
                "events": [],
                "notice": None,
                "observation_id": record.id,
            }
            if result != "seen":
                session.commit()
                return response
            row = session.get(ObservationStateRow, device_id)
            if row is None:
                row = ObservationStateRow(device_id=device_id, state=None, memory={}, updated_at=at)
                session.add(row)
            current = situation(observation["scene"], observation["detail"], at)
            events = changes(row.state, current)
            memory = row.memory or {}
            policy = policy_for(session, device_id)
            allowed = not policy.quiet(now) and (
                notices_last_hour(session, device_id, now) < policy.max_per_hour
            )
            worth = [event for event in events if worth_saying(event, memory, at)]
            # One notice per photo; a greeting comes first and mentions the rest.
            said = (
                sorted(worth, key=lambda event: event["kind"] != "arrival")[:1] if allowed else []
            )
            data = {"observation_id": record.id, "trigger": trigger}
            logged = {}
            for event in events:
                extra = {}
                if event["kind"] == "arrival" and memory.get("empty_since"):
                    extra["away_since"] = memory["empty_since"]
                if event in said:
                    extra["others"] = [
                        EVENT_WORDS[other["kind"]]
                        + (f"：{other['detail']}" if other["detail"] else "")
                        for other in events
                        if other is not event and other["kind"] != "start"
                    ]
                logged[id(event)] = log_event(
                    session,
                    device_id,
                    at,
                    event["kind"],
                    event.get("detail", ""),
                    spoken=event in said,
                    data=data | extra,
                )
            for event in said:
                session.add(
                    TaskMemoryRow(
                        id=uuid4().hex,
                        device_id=device_id,
                        rule_id=OBSERVE,
                        event_id=logged[id(event)].id,
                        event_version=1,
                        event_status=event["kind"],
                        status="queued",
                        created_at=now,
                        expires_at=now + NOTICE_TTL,
                        response=None,
                        lease_until=None,
                    )
                )
            row.state = current
            row.memory = remember(memory, events, said, at)
            row.updated_at = at
            session.commit()
            response.update(
                events=[event["kind"] for event in events],
                notice=said[0]["kind"] if said else None,
                situation=scene_text(observation["scene"]),
            )
            return response
