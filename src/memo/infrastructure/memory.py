from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from memo.domain.entities import Conflict, NotFound
from memo.domain.environment import LEVELS, SUBJECTS
from memo.domain.interaction import EventContext, Observation, Personality, Tone
from memo.domain.memory import (
    COMPRESS_AT,
    EARLIER_EPISODES,
    EPISODE_GAP,
    is_empty,
    trailing_episode,
)
from memo.infrastructure.database import (
    ConversationRow,
    DeviceRow,
    DeviceSettingRow,
    EventStateRow,
    MemorySummaryRow,
    ReadingRow,
    ScheduledJobRow,
    TaskMemoryRow,
)
from memo.infrastructure.environment_events import (
    SCHEDULE_RULE,
    attention_context,
    current_task,
)
from memo.infrastructure.event_history import current_status
from memo.infrastructure.memory_compression import (
    latest_summary,
    local_label,
    stored_profile,
    turns_after,
)
from memo.infrastructure.schedules import ScheduleRepository, job_description


def severity(row) -> int:
    level = row.state.get("level")
    return LEVELS.index(level) if level in LEVELS else -1


class SqlMemoryRepository:
    def __init__(self, session, tz: str = "Asia/Taipei"):
        self.session = session
        self.tz = tz
        self.schedules = ScheduleRepository(session, tz)

    def require_device(self, device_id):
        if self.session.get(DeviceRow, device_id) is None:
            raise NotFound("Device does not exist")

    def history(self, device_id, session_id, limit=20):
        self.require_device(device_id)
        rows = self.session.scalars(
            select(ConversationRow)
            .where(ConversationRow.device_id == device_id, ConversationRow.session_id == session_id)
            .order_by(ConversationRow.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "request_id": r.request_id,
                "user_text": r.user_text,
                "response": r.response,
                "created_at": r.created_at,
            }
            for r in reversed(rows)
        ]

    def tasks(self, device_id, limit=30):
        self.require_device(device_id)
        return self.session.scalars(
            select(TaskMemoryRow)
            .where(TaskMemoryRow.device_id == device_id)
            .order_by(TaskMemoryRow.created_at.desc(), TaskMemoryRow.id)
            .limit(limit)
        ).all()

    def context(self, device_id, session_id):
        self.require_device(device_id)
        self.session.expire_all()
        latest = self.session.scalar(
            select(ReadingRow)
            .where(ReadingRow.device_id == device_id)
            .order_by(ReadingRow.observed_at.desc(), ReadingRow.message_id)
            .limit(1)
        )
        observation = (
            Observation(
                latest.observed_at,
                {
                    "temperature_c": latest.temperature_c,
                    "humidity_pct": latest.humidity_pct,
                    "co2_ppm": latest.co2_ppm,
                    "illuminance_lux": latest.illuminance_lux,
                },
                latest.quality,
            )
            if latest
            else None
        )
        states = self.session.scalars(
            select(EventStateRow)
            .where(EventStateRow.device_id == device_id)
            .order_by(EventStateRow.rule_id)
        ).all()
        now = datetime.now(timezone.utc).timestamp()
        live = [
            r
            for r in states
            if r.state.get("status") in ("active", "recovering")
            and 0 <= now - r.state.get("last", 0) <= 30
        ]
        # The most severe live situation leads the conversation.
        active = max(live, key=severity) if live else None
        event = (
            EventContext(
                active.state["event_id"],
                active.state["version"],
                active.state["status"],
                active.rule_id + ": server validated",
            )
            if active
            else None
        )
        history, conversation_summary, earlier = self.conversation(
            device_id, session_id, datetime.now(timezone.utc)
        )
        profile = self.profile(device_id, session_id)
        tasks = tuple(
            {
                "id": t.id,
                "rule_id": t.rule_id,
                "event_status": t.event_status,
                "status": t.status,
                "event_version": t.event_version,
            }
            for t in self.tasks(device_id, 10)
        )
        attention = attention_context(active.rule_id, active.state) if active else None
        jobs = tuple(job_description(job, self.tz) for job in self.schedules.open_jobs(device_id))
        # End the read transaction before a potentially slow model request.
        self.session.rollback()
        return dict(
            observation=observation,
            event=event,
            history=history,
            conversation_summary=conversation_summary,
            earlier=earlier,
            profile=profile,
            task_memory=tasks,
            attention=attention,
            jobs=jobs,
        )

    def conversation(self, device_id, session_id, now):
        """What MEMO remembers of this conversation.

        After a pause longer than EPISODE_GAP a new conversation starts with no verbatim
        history. Within one, MEMO sees the latest turns as they were said, the running
        summary of its older part, and short summaries of a few earlier conversations."""
        last = latest_summary(self.session, device_id, session_id)
        pending = turns_after(
            self.session, device_id, session_id, last.last_turn_id if last else 0, limit=60
        )
        newest = pending[-1].at if pending else (last.ended_at if last else None)
        recent, summary = [], None
        if newest is not None and now - newest <= EPISODE_GAP:
            episode = trailing_episode(pending)
            recent = episode[-COMPRESS_AT:]
            # The running summary belongs to this conversation only if nothing broke it off.
            continues = (
                last is not None
                and last.kind == "running"
                and (not episode or episode[0] == pending[0])
                and (not episode or episode[0].at - last.ended_at <= EPISODE_GAP)
            )
            summary = last.summary if continues else None
        rows = self.session.scalars(
            select(MemorySummaryRow)
            .where(
                MemorySummaryRow.device_id == device_id,
                MemorySummaryRow.session_id == session_id,
                MemorySummaryRow.kind == "episode",
            )
            .order_by(MemorySummaryRow.last_turn_id.desc())
            .limit(EARLIER_EPISODES)
        ).all()
        earlier = tuple(
            {"when": local_label(row.ended_at, self.tz), "summary": row.summary}
            for row in reversed(rows)
        )
        history = []
        for turn in recent:
            history += [
                {"role": "user", "content": turn.user + ("（附了一張圖片）" if turn.image else "")},
                {"role": "assistant", "content": turn.memo},
            ]
        return tuple(history), summary, earlier

    def profile(self, device_id, session_id) -> dict | None:
        """What the person said about themselves in this memory scope, or None."""
        profile = stored_profile(self.session, device_id, session_id)
        return None if is_empty(profile) else profile

    def latest_profile(self, device_id) -> dict | None:
        """The profile of the conversation most recently active on this device, for notices."""
        scope = self.session.scalar(
            select(ConversationRow.session_id)
            .where(ConversationRow.device_id == device_id)
            .order_by(ConversationRow.id.desc())
            .limit(1)
        )
        return self.profile(device_id, scope) if scope else None

    def personality(self, device_id) -> Personality:
        row = self.session.get(DeviceSettingRow, device_id)
        if row is None:
            return Personality()
        saved = dict(row.personality)
        saved["tone"] = Tone(saved.get("tone", Tone.GENTLE.value))
        return Personality(**saved)

    def save_personality(self, device_id, personality: Personality):
        """Remember the persona last used in chat; proactive notices speak with it."""
        self.require_device(device_id)
        value = asdict(personality) | {"tone": personality.tone.value}
        row = self.session.get(DeviceSettingRow, device_id)
        now = datetime.now(timezone.utc)
        if row is None:
            self.session.add(
                DeviceSettingRow(device_id=device_id, personality=value, updated_at=now)
            )
        elif row.personality != value:
            row.personality, row.updated_at = value, now
        self.session.commit()

    def preferences(self, device_id) -> dict:
        self.require_device(device_id)
        row = self.session.get(DeviceSettingRow, device_id)
        return dict((row.preferences if row else None) or {})

    def save_settings(self, device_id, personality: Personality, preferences: dict):
        """Explicit settings; later chat messages still update the persona they use."""
        self.require_device(device_id)
        value = asdict(personality) | {"tone": personality.tone.value}
        row = self.session.get(DeviceSettingRow, device_id)
        now = datetime.now(timezone.utc)
        if row is None:
            row = DeviceSettingRow(device_id=device_id)
            self.session.add(row)
        row.personality, row.preferences, row.updated_at = value, preferences, now
        self.session.commit()

    def apply_action(self, device_id, action, session_id=None) -> str | None:
        return self.schedules.apply(device_id, action, datetime.now(timezone.utc), session_id)

    def missed(self, device_id, since) -> tuple[dict, ...]:
        """Notices created since ``since`` that can no longer reach the user.

        Still-deliverable notices are excluded: the page announces those itself."""
        self.require_device(device_id)
        rows = self.session.scalars(
            select(TaskMemoryRow)
            .where(
                TaskMemoryRow.device_id == device_id,
                TaskMemoryRow.created_at >= since,
                TaskMemoryRow.status.in_(["expired", "cancelled"]),
            )
            .order_by(TaskMemoryRow.created_at)
            .limit(30)
        ).all()
        missed = []
        for task in rows:
            item = {"at": task.created_at.isoformat(), "notice": task.event_status}
            if task.rule_id == SCHEDULE_RULE:
                job = self.session.get(ScheduledJobRow, task.event_id)
                item["what"] = job.message if job else "排程提醒"
            else:
                item["what"] = SUBJECTS.get(task.rule_id, task.rule_id)
            missed.append(item)
        self.session.rollback()
        return tuple(missed)

    def recall(self, device_id, session_id, request_id, text):
        row = self.session.scalar(
            select(ConversationRow).where(
                ConversationRow.device_id == device_id,
                ConversationRow.session_id == session_id,
                ConversationRow.request_id == request_id,
            )
        )
        if row and row.user_text != text:
            raise Conflict("request_id already belongs to a different message")
        result = row.response if row else None
        self.session.rollback()
        return result

    def remember(self, device_id, session_id, request_id, text, response):
        self.session.add(
            ConversationRow(
                device_id=device_id,
                session_id=session_id,
                request_id=request_id,
                user_text=text,
                response=response,
                created_at=datetime.now(timezone.utc),
            )
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.recall(device_id, session_id, request_id, text)
            if existing is None:
                raise
            return existing
        return response

    def clear_history(self, device_id, session_id):
        self.require_device(device_id)
        self.session.execute(
            delete(ConversationRow).where(
                ConversationRow.device_id == device_id, ConversationRow.session_id == session_id
            )
        )
        # Summaries of a cleared conversation go too; the profile is kept (cleared on its own).
        self.session.execute(
            delete(MemorySummaryRow).where(
                MemorySummaryRow.device_id == device_id, MemorySummaryRow.session_id == session_id
            )
        )
        self.session.commit()

    def events(self, device_id):
        self.require_device(device_id)
        rows = self.session.scalars(
            select(EventStateRow).where(EventStateRow.device_id == device_id)
        )
        now = datetime.now(timezone.utc).timestamp()
        return [
            {
                "rule_id": r.rule_id,
                **{k: v for k, v in r.state.items() if k != "samples"},
                "status": current_status(r.rule_id, r.state, now),
            }
            for r in rows
        ]

    def notifications(self, device_id):
        self.require_device(device_id)
        now = datetime.now(timezone.utc)
        tasks = self.session.scalars(
            select(TaskMemoryRow)
            .where(TaskMemoryRow.device_id == device_id, TaskMemoryRow.status == "ready")
            .order_by(TaskMemoryRow.created_at, TaskMemoryRow.id)
            .limit(50)
        )
        return [t for t in tasks if current_task(self.session, t, now)]

    def acknowledge(self, device_id, task_id):
        self.require_device(device_id)
        self.session.execute(
            update(DeviceRow).where(DeviceRow.id == device_id).values(name=DeviceRow.name)
        )
        task = self.session.get(TaskMemoryRow, task_id)
        if task is None or task.device_id != device_id:
            raise NotFound("Task not found")
        if task.status == "acknowledged":
            return {"status": "acknowledged"}
        if task.status != "ready" or not current_task(
            self.session, task, datetime.now(timezone.utc)
        ):
            raise Conflict("Notification is no longer current")
        task.status = "acknowledged"
        self.session.commit()
        return {"status": "acknowledged"}
