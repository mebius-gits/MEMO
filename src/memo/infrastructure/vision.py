"""Look tasks at run time: photo requests, analysis, comparison, records and retention.

A due look job becomes a task_memory row that waits for a photo (awaiting_capture). The web
page polls for those, takes a photo and posts it; the vision model answers the job's
question and describes the scene, and ``judge`` compares the answer with what MEMO saw
before. A new situation needs a second photo, taken a few seconds after the first, to agree
before MEMO speaks; the task is then queued and the ordinary dispatcher words the notice.

A watched change task (look.watch) can also be run on demand: the page watches the region
between runs and triggers a run when the region changed and settled; having seen the change
hold, it needs no confirming photo.

Photos are never stored. Every run leaves a record, including runs without a usable photo
(camera off, nothing sent in time, model failure), so the history has no gaps. Quiet hours
do not stop photos: MEMO keeps watching and recording, and only a watched object that
changed is said out loud.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update

from memo.domain.entities import Conflict, NotFound
from memo.domain.schedule import describe
from memo.domain.vision import (
    CAPTURE_WINDOW_SECONDS,
    CONFIRM_GAP_SECONDS,
    CONFIRM_WINDOW_SECONDS,
    QUALITY_WORDS,
    alerting,
    clean_quality,
    judge,
    quality_flags,
    scene_text,
)
from memo.infrastructure.database import (
    ScheduledJobRow,
    TaskMemoryRow,
    VisionDailyRow,
    VisionObservationRow,
)
from memo.infrastructure.environment_events import NOTICE_TTL, SCHEDULE_RULE, policy_for
from memo.infrastructure.observation import log_event
from memo.infrastructure.vision_model import VisionFailure

LOOK = "look"  # task_memory.event_status of a look run
WAITING = ("awaiting_capture", "confirming")
# Why the page sent no photo; anything else it reports is recorded as camera_unavailable.
SKIPS = ("camera_off", "camera_unavailable", "permission_denied")
OBSERVATION_DAYS = 30
DAILY_DAYS = 180
# How far a photo's own timestamp may differ from the server's before the server's is used.
CLOCK_SLACK = timedelta(seconds=180)
# A watched task is run on demand at most this often.
TRIGGER_GAP = timedelta(seconds=20)


def local_day(at: datetime, tz: str) -> str:
    return at.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def local_clock(at: datetime, tz: str) -> str:
    return at.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def record(session, job, task_id, run, attempt, at, result, **fields) -> VisionObservationRow:
    look = job.look or {}
    row = VisionObservationRow(
        id=uuid4().hex,
        device_id=job.device_id,
        job_id=job.id,
        task_id=task_id,
        run=run,
        attempt=attempt,
        captured_at=at,
        mode=look.get("mode", "check"),
        question=look.get("question", job.message),
        result=result,
        created_at=datetime.now(timezone.utc),
        **fields,
    )
    session.add(row)
    return row


def count_daily(session, job, at, tz, result, *, notice=False, streak=0):
    """Add one run to the job's daily counts, by its final answer."""
    key = (job.id, local_day(at, tz))
    row = session.get(VisionDailyRow, key)
    if row is None:
        row = VisionDailyRow(
            job_id=key[0],
            day=key[1],
            device_id=job.device_id,
            counts={},
            notices=0,
            longest_streak=0,
            updated_at=at,
        )
        session.add(row)
    row.counts = row.counts | {result: row.counts.get(result, 0) + 1}
    if notice:
        row.notices = (row.notices or 0) + 1
    if result in ("yes", "no") and alerting(
        job.look or {"mode": "check", "alert_on": "yes"}, result
    ):
        row.first_alert_at = row.first_alert_at or at
        row.last_alert_at = at
    row.longest_streak = max(row.longest_streak or 0, streak)
    row.updated_at = at


def skip_run(session, job, task_id, run, at, tz, reason, attempt=1, decision="skipped"):
    record(session, job, task_id, run, attempt, at, "skipped", reason=reason, decision=decision)
    count_daily(session, job, at, tz, "skipped")


def start_run(session, job, run, now, tz, unavailable=None):
    """A look job is due: ask the page for a photo, or record why none will be taken."""
    if unavailable:
        skip_run(session, job, None, run, now, tz, unavailable)
        return None
    task = TaskMemoryRow(
        id=uuid4().hex,
        device_id=job.device_id,
        rule_id=SCHEDULE_RULE,
        event_id=job.id,
        event_version=run,
        event_status=LOOK,
        status="awaiting_capture",
        created_at=now,
        expires_at=now + timedelta(seconds=CAPTURE_WINDOW_SECONDS),
        response=None,
        lease_until=None,
    )
    session.add(task)
    return task


def trigger_run(session, job, now) -> TaskMemoryRow:
    """Run a watched task now: the page saw its region change and settle."""
    if not (job.look or {}).get("watch") or job.status != "active":
        raise Conflict("Only an active watched task can be run on demand")
    latest = session.scalar(
        select(TaskMemoryRow)
        .where(TaskMemoryRow.event_id == job.id, TaskMemoryRow.event_status == LOOK)
        .order_by(TaskMemoryRow.created_at.desc())
        .limit(1)
    )
    if latest is not None and (
        latest.status in (*WAITING, "analyzing") or now - latest.created_at < TRIGGER_GAP
    ):
        raise Conflict("This task is already running or ran a moment ago")
    job.runs += 1
    job.updated_at = now
    return start_run(session, job, job.runs, now, None)


def waiting_tasks(session, device_id=None):
    query = select(TaskMemoryRow).where(
        TaskMemoryRow.rule_id == SCHEDULE_RULE,
        TaskMemoryRow.event_status == LOOK,
        TaskMemoryRow.status.in_([*WAITING, "analyzing"]),
    )
    if device_id is not None:
        query = query.where(TaskMemoryRow.device_id == device_id)
    return session.scalars(query.order_by(TaskMemoryRow.created_at, TaskMemoryRow.id)).all()


def pending(session, device_id, now) -> dict:
    """Photos the page should take now, and whether the device has look tasks at all
    (the page keeps the camera on in the background only while it does)."""
    requests = []
    for task in waiting_tasks(session, device_id):
        if task.status == "analyzing" or task.expires_at <= now:
            continue
        job = session.get(ScheduledJobRow, task.event_id)
        if job is None or job.status == "cancelled" or not job.look:
            continue
        state = job.look_state or {}
        requests.append(
            {
                "task_id": task.id,
                "job_id": job.id,
                "attempt": 2 if task.status == "confirming" else 1,
                "mode": job.look["mode"],
                "question": job.look["question"],
                "roi": job.look.get("roi"),
                # A change task with no baseline yet: this photo becomes the reference.
                "baseline": job.look["mode"] == "change" and not state.get("baseline_text"),
                "expires_at": task.expires_at,
            }
        )
    jobs = session.scalars(
        select(ScheduledJobRow).where(
            ScheduledJobRow.device_id == device_id,
            ScheduledJobRow.purpose == LOOK,
            ScheduledJobRow.status == "active",
        )
    ).all()
    # Watched tasks: the page keeps comparing these regions between runs.
    watching = [
        {"job_id": job.id, "question": job.look["question"], "roi": job.look.get("roi")}
        for job in jobs
        if (job.look or {}).get("watch")
    ]
    session.rollback()
    return {"requests": requests, "active_jobs": len(jobs), "watching": watching}


def expire_waiting(session, now, tz) -> int:
    """Close look runs that never got their photo, or whose analysis was interrupted."""
    closed = 0
    for task in waiting_tasks(session):
        if task.status == "analyzing":
            if task.lease_until is None or task.lease_until >= now:
                continue
        elif task.expires_at > now:
            continue
        job = session.get(ScheduledJobRow, task.event_id)
        status, task.status = task.status, "skipped"
        closed += 1
        if job is None:
            continue
        run = task.event_version
        if status == "awaiting_capture":
            skip_run(session, job, task.id, run, now, tz, "no_capture")
        elif status == "confirming":
            # The confirming photo never came: the first answer stays unconfirmed.
            first = first_answer(session, task.id)
            record(
                session,
                job,
                task.id,
                run,
                2,
                now,
                "skipped",
                reason="no_confirmation",
                decision="unconfirmed",
            )
            count_daily(session, job, now, tz, first or "skipped")
        else:
            skip_run(session, job, task.id, run, now, tz, "analysis_interrupted")
    session.commit()
    return closed


def first_answer(session, task_id) -> str | None:
    return session.scalar(
        select(VisionObservationRow.result)
        .where(VisionObservationRow.task_id == task_id, VisionObservationRow.attempt == 1)
        .order_by(VisionObservationRow.created_at.desc())
        .limit(1)
    )


def prune(session, now, tz):
    """Photo records are kept 30 days, daily counts 180."""
    session.execute(
        delete(VisionObservationRow).where(
            VisionObservationRow.captured_at < now - timedelta(days=OBSERVATION_DAYS)
        )
    )
    session.execute(
        delete(VisionDailyRow).where(
            VisionDailyRow.day < local_day(now - timedelta(days=DAILY_DAYS), tz)
        )
    )
    session.commit()


def forget(session, job_id):
    """A cancelled look task takes its records with it."""
    session.execute(delete(VisionObservationRow).where(VisionObservationRow.job_id == job_id))
    session.execute(delete(VisionDailyRow).where(VisionDailyRow.job_id == job_id))


def look_notices_last_hour(session, device_id, now) -> int:
    return session.scalar(
        select(func.count())
        .select_from(TaskMemoryRow)
        .where(
            TaskMemoryRow.device_id == device_id,
            TaskMemoryRow.rule_id == SCHEDULE_RULE,
            TaskMemoryRow.event_status == LOOK,
            TaskMemoryRow.status.in_(["queued", "generating", "ready", "acknowledged", "expired"]),
            TaskMemoryRow.created_at >= now - timedelta(hours=1),
        )
    )


# ---- What MEMO remembers ---------------------------------------------------------------


def brief(row: VisionObservationRow, tz: str) -> dict:
    quality = row.quality or {}
    item = {"at": local_clock(row.captured_at, tz), "result": row.result}
    if row.detail:
        item["detail"] = row.detail
    if scene := scene_text(row.scene):
        item["scene"] = scene
    if words := [QUALITY_WORDS[flag] for flag in quality.get("flags", []) if flag in QUALITY_WORDS]:
        item["quality"] = words
    if row.reason:
        item["reason"] = row.reason
    return item


def settled(rows):
    """Records that stand for a run's answer: not a first photo still awaiting confirmation."""
    return [row for row in rows if row.decision != "confirm"]


def job_facts(session, job, tz, now) -> dict:
    look, state = job.look or {}, job.look_state or {}
    rows = settled(
        session.scalars(
            select(VisionObservationRow)
            .where(VisionObservationRow.job_id == job.id)
            .order_by(VisionObservationRow.captured_at.desc(), VisionObservationRow.created_at)
            .limit(8)
        ).all()
    )
    seen = next((row for row in rows if row.result != "skipped"), None)
    today = session.get(VisionDailyRow, (job.id, local_day(now, tz)))
    facts = {
        "question": look.get("question", job.message),
        "mode": look.get("mode"),
        "schedule": describe(
            job.kind, job.purpose, job.message, job.minutes, job.time_of_day, job.next_run_at, tz
        ),
        "status": job.status,
        "last": brief(seen, tz) if seen else None,
        "recent": [brief(row, tz) for row in rows[:3]],
        "today": (today.counts | {"notices": today.notices}) if today else {},
    }
    if look.get("mode") == "check":
        facts["alert_on"] = look.get("alert_on")
        facts["streak"] = state.get("alert_streak", 0)
    elif state.get("baseline_text"):
        facts["baseline"] = state["baseline_text"]
    if changed := state.get("changed_at"):
        facts["since_minutes"] = max(
            0, round((now - datetime.fromisoformat(changed)).total_seconds() / 60)
        )
    return facts


def device_facts(session, device_id, tz, now) -> tuple[dict, ...]:
    jobs = session.scalars(
        select(ScheduledJobRow)
        .where(
            ScheduledJobRow.device_id == device_id,
            ScheduledJobRow.purpose == LOOK,
            ScheduledJobRow.status.in_(["active", "paused"]),
        )
        .order_by(ScheduledJobRow.created_at)
        .limit(5)
    ).all()
    return tuple(job_facts(session, job, tz, now) for job in jobs)


def notice_context(session, job, task, tz, now) -> dict:
    """The facts a look notice is worded from: what the deciding photo showed, how it
    compares with before, how often today, and what MEMO already said about it."""
    look, state = job.look or {}, job.look_state or {}
    decisive = session.scalar(
        select(VisionObservationRow)
        .where(VisionObservationRow.task_id == task.id)
        .order_by(VisionObservationRow.attempt.desc(), VisionObservationRow.created_at.desc())
        .limit(1)
    )
    before = session.scalar(
        select(VisionObservationRow)
        .where(
            VisionObservationRow.job_id == job.id,
            VisionObservationRow.run < task.event_version,
            VisionObservationRow.result.in_(["yes", "no", "seen"]),
            VisionObservationRow.decision.not_in(["confirm", "unconfirmed"]),
        )
        .order_by(VisionObservationRow.run.desc())
        .limit(1)
    )
    previous = before.result if before else None
    # A JSON column stores a missing reply as JSON null, so the filter happens here.
    said = [
        row.response["text"]
        for row in session.scalars(
            select(TaskMemoryRow)
            .where(TaskMemoryRow.event_id == job.id, TaskMemoryRow.id != task.id)
            .order_by(TaskMemoryRow.created_at.desc())
            .limit(20)
        )
        if row.response and row.response.get("text")
    ][:3]
    today = session.get(VisionDailyRow, (job.id, local_day(now, tz)))
    counts = today.counts if today else {}
    flags = ((decisive.quality or {}).get("flags", []) if decisive else []) or []
    context = {
        "purpose": LOOK,
        "kind": job.kind,
        "message": job.message,
        "question": look.get("question", job.message),
        "mode": look.get("mode"),
        "notice": (decisive.notice if decisive else None) or "report",
        "level": (decisive.level if decisive else None) or 1,
        "result": decisive.result if decisive else None,
        "previous": previous,
        "previous_detail": before.detail if before else None,
        "detail": decisive.detail if decisive else None,
        "scene": scene_text(decisive.scene) if decisive else "",
        "quality": [QUALITY_WORDS[flag] for flag in flags if flag in QUALITY_WORDS],
        "streak": state.get("alert_streak", 0),
        "today_count": counts.get(
            "yes" if look.get("mode") == "change" else look.get("alert_on", "yes"), 0
        ),
        "recent_notices": said,
        "captured_at": local_clock(decisive.captured_at, tz) if decisive else None,
    }
    if changed := state.get("changed_at"):
        context["since_minutes"] = max(
            0, round((now - datetime.fromisoformat(changed)).total_seconds() / 60)
        )
    return context


# ---- Taking a photo ----------------------------------------------------------------------


class CaptureService:
    """Receives a look run's photo (or the reason there is none) and settles the run."""

    def __init__(self, sessions, analyzer, tz: str = "Asia/Taipei"):
        self.sessions = sessions
        self.analyzer = analyzer
        self.tz = tz

    def trigger(self, device_id, job_id) -> dict:
        """Start a run of a watched task now; returns the photo request, as pending lists it."""
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            job = session.get(ScheduledJobRow, job_id)
            if job is None or job.device_id != device_id or job.purpose != LOOK:
                raise NotFound("Look task not found")
            task = trigger_run(session, job, now)
            session.commit()
            return {
                "task_id": task.id,
                "job_id": job.id,
                "attempt": 1,
                "mode": job.look["mode"],
                "question": job.look["question"],
                "roi": job.look.get("roi"),
                "baseline": not (job.look_state or {}).get("baseline_text"),
                "expires_at": task.expires_at,
            }

    def claim(self, device_id, task_id, attempt, now):
        """Take the run for this photo; a second sender (another tab, a retry) gets Conflict."""
        with self.sessions() as session:
            task = session.get(TaskMemoryRow, task_id)
            if (
                task is None
                or task.device_id != device_id
                or task.rule_id != SCHEDULE_RULE
                or task.event_status != LOOK
            ):
                raise NotFound("Look task not found")
            expected = "confirming" if attempt == 2 else "awaiting_capture"
            if task.status != expected or task.expires_at <= now:
                raise Conflict("This photo is no longer wanted")
            job = session.get(ScheduledJobRow, task.event_id)
            if job is None or job.status == "cancelled" or not job.look:
                task.status = "cancelled"
                session.commit()
                raise Conflict("The look task was cancelled")
            lease = now + timedelta(seconds=self.analyzer.timeout * 2 + 15)
            claimed = session.execute(
                update(TaskMemoryRow)
                .where(TaskMemoryRow.id == task_id, TaskMemoryRow.status == expected)
                .values(status="analyzing", lease_until=lease)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                session.rollback()
                raise Conflict("This photo is already being handled")
            session.commit()
            first = first_answer(session, task_id) if attempt == 2 else None
            session.rollback()
            return dict(look=job.look, state=job.look_state or {}, run=task.event_version), (
                lease,
                first,
            )

    def submit(
        self,
        device_id: str,
        task_id: str,
        attempt: int = 1,
        *,
        image: str | None = None,
        reference: str | None = None,
        quality: dict | None = None,
        captured_at: datetime | None = None,
        skip: str | None = None,
        trigger: str | None = None,
        moment: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        if captured_at is None or not now - CLOCK_SLACK <= captured_at <= now + timedelta(
            seconds=5
        ):
            captured_at = now
        job_view, (lease, first) = self.claim(device_id, task_id, attempt, now)
        look, state = job_view["look"], job_view["state"]
        metrics = clean_quality(quality)
        flags = quality_flags(metrics)
        baseline = (
            not skip
            and look["mode"] == "change"
            and not reference
            and not state.get("baseline_text")
        )
        reason = observation = None
        if skip or not image:
            reason = skip if skip in SKIPS else "camera_unavailable"
        else:
            try:
                observation = self.analyzer.analyze(
                    device_id,
                    look,
                    image,
                    reference=reference,
                    baseline_text=state.get("baseline_text"),
                    baseline=baseline,
                    captured_label=captured_at.astimezone(ZoneInfo(self.tz)).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    moment=moment,
                )
            except VisionFailure as failure:
                reason = failure.reason
        return self.settle(
            device_id,
            task_id,
            attempt,
            lease,
            first,
            captured_at,
            observation,
            reason,
            metrics,
            flags,
            baseline=baseline and observation is not None,
            had_reference=bool(reference),
            skipped=bool(skip or not image),
            # Watching already saw the change hold, so the first photo is trusted.
            confirmed=trigger == "watch" and bool(look.get("watch")),
        )

    def settle(
        self,
        device_id,
        task_id,
        attempt,
        lease,
        first,
        at,
        observation,
        reason,
        metrics,
        flags,
        *,
        baseline,
        had_reference,
        skipped,
        confirmed=False,
    ) -> dict:
        tz = self.tz
        with self.sessions() as session:
            task = session.get(TaskMemoryRow, task_id)
            if task.status != "analyzing" or task.lease_until != lease:
                raise Conflict("This photo was superseded")
            job = session.get(ScheduledJobRow, task.event_id)
            if job is None or job.status == "cancelled":
                task.status = "cancelled"
                session.commit()
                return {"status": "cancelled", "decision": None, "store_reference": False}
            look = job.look
            run = task.event_version
            quality = {"metrics": metrics, "flags": flags}
            if observation and observation.get("visibility"):
                quality["visibility"] = observation["visibility"]
            if skipped:
                decision = "unconfirmed" if attempt == 2 else "skipped"
                row = record(
                    session,
                    job,
                    task_id,
                    run,
                    attempt,
                    at,
                    "skipped",
                    reason=reason,
                    decision=decision,
                    quality=quality,
                )
                count_daily(session, job, at, tz, first if attempt == 2 and first else "skipped")
                task.status = "skipped"
                session.commit()
                return {
                    "status": "skipped",
                    "decision": decision,
                    "result": "skipped",
                    "reason": reason,
                    "observation_id": row.id,
                    "store_reference": False,
                }
            result = observation["result"] if observation else "unknown"
            scene = observation["scene"] if observation else None
            verdict = judge(
                look,
                job.look_state,
                result,
                at.isoformat(),
                attempt=attempt,
                first=first,
                scene_line=scene_text(scene) or (observation or {}).get("detail") or "",
                baseline=baseline,
                confirmed=confirmed,
            )
            decision = verdict.decision
            if decision == "notify":
                policy = policy_for(session, device_id)
                now = datetime.now(timezone.utc)
                # A watched object that changed is said even in quiet hours; the rest waits.
                urgent = look["mode"] == "change"
                if not urgent and (
                    policy.quiet(now)
                    or look_notices_last_hour(session, device_id, now) >= policy.max_per_hour
                ):
                    decision = "muted"
            row = record(
                session,
                job,
                task_id,
                run,
                attempt,
                at,
                result,
                detail=(observation or {}).get("detail"),
                scene=scene,
                quality=quality,
                reason=reason,
                decision=decision,
                notice=verdict.notice,
                level=verdict.level,
            )
            job.look_state = verdict.state
            job.updated_at = at
            if decision in ("notify", "muted"):
                # The observation log keeps what look tasks found, said or not.
                log_event(
                    session,
                    device_id,
                    at,
                    "watched_change" if look["mode"] == "change" else "task",
                    f"{look['question']}：{(observation or {}).get('detail') or result}",
                    spoken=decision == "notify",
                    data={"job_id": job.id, "observation_id": row.id, "notice": verdict.notice},
                )
            store_reference = verdict.store_reference or (
                look["mode"] == "change"
                and not had_reference
                and result == "no"
                and decision in ("done", "notify", "muted")
            )
            if decision == "confirm":
                task.status = "confirming"
                task.expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=CONFIRM_WINDOW_SECONDS
                )
            elif decision == "notify":
                task.status = "queued"
                task.lease_until = None
                task.expires_at = datetime.now(timezone.utc) + NOTICE_TTL
            else:
                task.status = "done"
            if decision != "confirm":
                count_daily(
                    session,
                    job,
                    at,
                    tz,
                    result,
                    notice=decision == "notify",
                    streak=verdict.state.get("alert_streak", 0),
                )
            session.commit()
            response = {
                "status": task.status,
                "decision": decision,
                "result": result,
                "notice": verdict.notice if decision == "notify" else None,
                "level": verdict.level if decision == "notify" else None,
                "reason": reason,
                "quality_flags": flags,
                "observation_id": row.id,
                "store_reference": store_reference,
            }
            if decision == "confirm":
                response["confirm_after_seconds"] = CONFIRM_GAP_SECONDS
            return response
