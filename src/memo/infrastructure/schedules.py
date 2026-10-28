"""Scheduled jobs: storage, chat actions, and the background loop that fires due runs.

A due run becomes an ordinary task_memory notice (rule_id "schedule"), so the existing
dispatcher, web bubble, speech and BMO expression handle it like any proactive notice.
A look job's run first waits for the web page's photo (memo.infrastructure.vision).
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update

from memo.domain.schedule import describe, first_run, following_run
from memo.infrastructure.database import (
    EventStateRow,
    ReadingRow,
    ScheduledJobRow,
    TaskMemoryRow,
)
from memo.infrastructure.environment_events import (
    NOTICE_TTL,
    SCHEDULE_RULE,
    log_update,
    reading_metrics,
    sweep_stale,
)
from memo.infrastructure.observation import prune as prune_log
from memo.infrastructure.vision import LOOK, expire_waiting, forget, prune, running, start_run

logger = logging.getLogger(__name__)
OPEN = ("active", "paused")


def job_description(job: ScheduledJobRow, tz: str) -> str:
    text = describe(
        job.kind,
        job.purpose,
        job.message,
        job.minutes,
        job.time_of_day,
        job.next_run_at,
        tz,
        job.look,
    )
    return text + ("（已暫停）" if job.status == "paused" else "")


def latest_metrics(session, device_id) -> dict | None:
    latest = session.scalar(
        select(ReadingRow)
        .where(ReadingRow.device_id == device_id)
        .order_by(ReadingRow.observed_at.desc(), ReadingRow.message_id)
        .limit(1)
    )
    if latest is None:
        return None
    return {key: value for key, value in reading_metrics(latest).items() if value is not None} | {
        "observed_at": latest.observed_at.isoformat()
    }


class ScheduleRepository:
    def __init__(self, session, tz: str = "Asia/Taipei"):
        self.session = session
        self.tz = tz

    def open_jobs(self, device_id) -> list[ScheduledJobRow]:
        return self.session.scalars(
            select(ScheduledJobRow)
            .where(ScheduledJobRow.device_id == device_id, ScheduledJobRow.status.in_(OPEN))
            .order_by(ScheduledJobRow.next_run_at, ScheduledJobRow.id)
        ).all()

    def create(self, device_id, action, now, session_id=None) -> ScheduledJobRow:
        job = ScheduledJobRow(
            id=uuid4().hex,
            device_id=device_id,
            kind=action["kind"],
            purpose=action["purpose"],
            message=action["message"],
            minutes=action.get("minutes"),
            time_of_day=action.get("time"),
            # A follow-up compares against what the air was like when the user acted.
            baseline=latest_metrics(self.session, device_id)
            if action["kind"] == "followup"
            else None,
            status="active",
            # A change task photographs its baseline right away; the first change counts.
            next_run_at=now
            if (action.get("look") or {}).get("mode") == "change"
            else first_run(action, now, self.tz),
            runs=0,
            session_id=session_id,
            created_at=now,
            updated_at=now,
            look=action.get("look"),
            look_state={} if action.get("look") else None,
        )
        self.session.add(job)
        return job

    def matching(self, device_id, match, statuses=OPEN) -> list[ScheduledJobRow]:
        return [
            job
            for job in self.session.scalars(
                select(ScheduledJobRow).where(
                    ScheduledJobRow.device_id == device_id, ScheduledJobRow.status.in_(statuses)
                )
            )
            if not match or match in job.message
        ]

    def snooze(self, device_id, minutes, now) -> list[str]:
        """Hold back reminders for live events; escalations still get through."""
        snoozed = []
        rows = self.session.scalars(
            select(EventStateRow).where(EventStateRow.device_id == device_id)
        ).all()
        for row in rows:
            if row.state.get("live") and row.state.get("status") in ("active", "recovering"):
                row.state = row.state | {"snoozed_until": now.timestamp() + minutes * 60}
                log_update(
                    self.session,
                    device_id,
                    row.rule_id,
                    row.state,
                    "snoozed",
                    now,
                    {"minutes": minutes, "via": "chat"},
                )
                snoozed.append(row.rule_id)
        return snoozed

    def apply(self, device_id, action, now, session_id=None) -> str | None:
        """Carry out a validated action; returns a Chinese description, or None if no-op."""
        kind = action["type"]
        if kind == "schedule":
            job = self.create(device_id, action, now, session_id)
            self.session.commit()
            return "已設定：" + job_description(job, self.tz)
        if kind == "snooze":
            rules = self.snooze(device_id, action["minutes"], now)
            self.session.commit()
            return f"已延後 {action['minutes']} 分鐘再提醒目前的狀況" if rules else None
        target = {"cancel": OPEN, "pause": ("active",), "resume": ("paused",)}[kind]
        jobs = self.matching(device_id, action["match"], target)
        if not jobs:
            return None
        status = {"cancel": "cancelled", "pause": "paused", "resume": "active"}[kind]
        for job in jobs:
            job.status, job.updated_at = status, now
            if status == "cancelled" and job.purpose == LOOK:
                forget(self.session, job.id)
            if kind == "resume" and job.next_run_at and job.next_run_at <= now:
                job.next_run_at = following_run(
                    job.kind, job.minutes, job.time_of_day, job.next_run_at, now, self.tz
                ) or (now + timedelta(minutes=1))
        self.session.commit()
        verb = {"cancel": "已取消", "pause": "已暫停", "resume": "已恢復"}[kind]
        return verb + "：" + "、".join(job.message or "環境摘要" for job in jobs)


def schedule_context(session, job: ScheduledJobRow, tz: str) -> dict:
    context = {
        "purpose": job.purpose,
        "kind": job.kind,
        "message": job.message,
        "created_at": job.created_at.isoformat(),
    }
    if job.purpose == "followup":
        current = latest_metrics(session, job.device_id)
        context |= {"baseline": job.baseline, "current": current}
        if job.baseline and current:
            context["change"] = {
                key: round(current[key] - job.baseline[key], 1)
                for key in ("temperature_c", "humidity_pct", "co2_ppm", "feels_like_c")
                if key in current and key in job.baseline
            }
    return context


def fire_due(sessions, now: datetime, tz: str, vision_unavailable: str | None = None) -> int:
    """Turn due runs into queued notices. The conditional update makes each run fire once,
    even with several API processes polling the same database. A look job's run asks the
    page for a photo instead, or records why none can be taken (``vision_unavailable``)."""
    fired = 0
    with sessions() as session:
        due = session.scalars(
            select(ScheduledJobRow)
            .where(ScheduledJobRow.status == "active", ScheduledJobRow.next_run_at <= now)
            .order_by(ScheduledJobRow.next_run_at)
            .limit(20)
        ).all()
        for job in due:
            following = following_run(
                job.kind, job.minutes, job.time_of_day, job.next_run_at, now, tz
            )
            if following is None and job.runs == 0 and (job.look or {}).get("mode") == "change":
                # A one-off change task: the first run was its baseline, now the comparison.
                # A sensor task's baseline is its only scheduled run; sensors start the rest.
                if job.kind != "event":
                    following = job.next_run_at + timedelta(minutes=job.minutes)
            run = job.runs + 1
            claimed = session.execute(
                update(ScheduledJobRow)
                .where(
                    ScheduledJobRow.id == job.id,
                    ScheduledJobRow.next_run_at == job.next_run_at,
                    ScheduledJobRow.status == "active",
                )
                .values(
                    next_run_at=following,
                    status="active" if following or job.kind == "event" else "done",
                    runs=run,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                continue
            if job.purpose == LOOK:
                # A run still waiting for its photo or the model covers this one: frequent
                # safety checks must not pile up behind a slow model.
                if not running(session, job.id):
                    start_run(session, job, run, now, tz, vision_unavailable)
                    fired += 1
                continue
            session.add(
                TaskMemoryRow(
                    id=uuid4().hex,
                    device_id=job.device_id,
                    rule_id=SCHEDULE_RULE,
                    event_id=job.id,
                    event_version=run,
                    event_status=job.purpose,
                    status="queued",
                    created_at=now,
                    expires_at=now + NOTICE_TTL,
                    response=None,
                    lease_until=None,
                )
            )
            fired += 1
        session.commit()
    return fired


def run_scheduler(stop, sessions, tz, vision_unavailable: str | None = None):
    """Every second: fire due jobs, close look runs that got no photo in time, and notice
    sensors that went silent between readings. Hourly: drop expired look records and log."""
    pruned = None
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        try:
            fire_due(sessions, now, tz, vision_unavailable)
        except Exception:
            logger.error("Scheduler pass failed; due jobs will be retried")
        try:
            with sessions() as session:
                expire_waiting(session, now, tz)
                if pruned is None or now - pruned >= timedelta(hours=1):
                    prune(session, now, tz)
                    prune_log(session, now)
                    session.commit()
                    pruned = now
        except Exception:
            logger.error("Look run upkeep failed; it will run again")
        try:
            sweep_stale(sessions, now)
        except Exception:
            logger.error("Stale sweep failed; it will run again")
        stop.wait(1)
