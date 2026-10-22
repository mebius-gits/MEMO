"""Durable task dispatcher. Model latency never blocks telemetry acknowledgement."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from memo.domain.interaction import EventContext, InteractionInput, Personality
from memo.infrastructure.database import DeviceRow, TaskMemoryRow
from memo.infrastructure.environment_events import current_task
from memo.infrastructure.memory import SqlMemoryRepository

logger = logging.getLogger(__name__)


def dispatch_one(sessions, interaction, timeout=30):
    now = datetime.now(timezone.utc)
    lease = now + timedelta(seconds=timeout + 15)
    with sessions() as session:
        session.execute(
            update(TaskMemoryRow)
            .where(
                TaskMemoryRow.expires_at <= now,
                TaskMemoryRow.status.in_(["queued", "generating", "ready"]),
            )
            .values(status="expired")
        )
        candidate = session.scalar(
            select(TaskMemoryRow)
            .where(
                or_(
                    TaskMemoryRow.status == "queued",
                    (TaskMemoryRow.status == "generating") & (TaskMemoryRow.lease_until < now),
                )
            )
            .order_by(TaskMemoryRow.created_at, TaskMemoryRow.id)
            .limit(1)
        )
        if candidate is None:
            session.commit()
            return False
        task_id = candidate.id
        claimed = session.execute(
            update(TaskMemoryRow)
            .where(
                TaskMemoryRow.id == task_id,
                or_(
                    TaskMemoryRow.status == "queued",
                    (TaskMemoryRow.status == "generating") & (TaskMemoryRow.lease_until < now),
                ),
            )
            .values(status="generating", lease_until=lease)
        )
        if claimed.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        task = session.get(TaskMemoryRow, task_id)
        if not current_task(session, task, now):
            task.status = "cancelled"
            session.commit()
            return True
        device_id = task.device_id
        event = EventContext(
            task.event_id,
            task.event_version,
            task.event_status,
            task.rule_id + ": server validated",
        )
        # Proactive tasks do not borrow an arbitrary person's conversation session.
        context = SqlMemoryRepository(session).context(device_id, "__proactive__")
        request = InteractionInput(
            user_message="請用 MEMO 的人格主動陪伴式提醒，不要報數字。",
            personality=Personality(),
            device_id=device_id,
            observation=context["observation"],
            event=event,
            context_source="stored_readings",
            proactive=True,
            task_memory=context["task_memory"],
        )
    reply = interaction.respond(interaction.prepare(request, now))
    with sessions() as session:
        session.execute(
            update(DeviceRow).where(DeviceRow.id == device_id).values(name=DeviceRow.name)
        )
        task = session.get(TaskMemoryRow, task_id)
        if task.status != "generating" or task.lease_until != lease:
            return True
        if not current_task(session, task, datetime.now(timezone.utc)):
            task.status = "cancelled"
        else:
            task.status = "ready"
            task.response = {
                "text": reply.text,
                "emotion": {"label": reply.emotion.value, "intensity": reply.intensity},
                "source": reply.source,
                "fallback_reason": reply.fallback_reason,
            }
        session.commit()
    return True


def run_dispatcher(stop, sessions, interaction, timeout):
    while not stop.is_set():
        try:
            worked = dispatch_one(sessions, interaction, timeout)
        except Exception:
            # Never log model payloads, conversations, or credentials.
            logger.error("Notification dispatch failed; durable task will be retried")
            worked = False
        stop.wait(0.1 if worked else 1)
