"""Durable task dispatcher. Model latency never blocks telemetry acknowledgement."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from memo.domain.environment import SENSOR_RULE, suggestions_for
from memo.domain.interaction import EventContext, InteractionInput
from memo.infrastructure.database import DeviceRow, EventStateRow, ScheduledJobRow, TaskMemoryRow
from memo.infrastructure.environment_events import SCHEDULE_RULE, attention_context, current_task
from memo.infrastructure.memory import SqlMemoryRepository
from memo.infrastructure.schedules import schedule_context

logger = logging.getLogger(__name__)

# What MEMO is asked to do for each kind of notice; the context JSON carries the facts.
NOTICE_PROMPTS = {
    "active": "請用你的人格主動提醒使用者剛觀察到的狀況（見 attention），一到兩句。",
    "escalated": (
        "狀況變得更嚴重，已到「{level_label}」程度。請比上次更明確地提醒，"
        "可以提一個最關鍵的數字，並從 approved_suggestions 挑一個建議。"
    ),
    "reminder": (
        "這個狀況已持續約 {minutes_active} 分鐘，這是第 {reminders} 次提醒。"
        "換個說法再提醒，不要重複上次的話；嚴重程度越高語氣越緊迫，"
        "可從 approved_suggestions 挑一個建議。"
    ),
    "resolved": "狀況已經恢復，用一句話簡短告訴使用者。",
}
SENSOR_PROMPTS = {
    "active": (
        "感測資料已經中斷超過 30 秒，你現在看不到任何環境數值。用一句話告訴使用者，"
        "說你會等資料回來；不要猜測原因，也不要引用舊的數值。"
    ),
    "resolved": "感測資料恢復了，用一句話告訴使用者你又看得到環境了。",
}
SCHEDULE_PROMPTS = {
    "remind": "到了使用者指定的提醒時間，請用你的人格提醒他：{message}。",
    "summary": "這是使用者要求的定時環境摘要，用一兩句說目前環境，可引用關鍵數值。",
    "followup": (
        "使用者先前說「{message}」。請根據 schedule 的 baseline、current 與 change，"
        "用一兩句回報有沒有改善，可引用數值；沒有前後資料就直說無法判斷。"
    ),
}


def notice_request(session, task, memory, tz):
    """Build the model input for a queued notice, environmental or scheduled."""
    device_id = task.device_id
    # Proactive tasks do not borrow an arbitrary person's conversation session.
    context = memory.context(device_id, "__proactive__")
    common = dict(
        personality=memory.personality(device_id),
        device_id=device_id,
        observation=context["observation"],
        context_source="stored_readings",
        proactive=True,
        task_memory=context["task_memory"],
        jobs=context["jobs"],
        # Notices may call the person by name; they never see conversation history.
        profile=memory.latest_profile(device_id),
    )
    if task.rule_id == SCHEDULE_RULE:
        job = session.get(ScheduledJobRow, task.event_id)
        return InteractionInput(
            user_message=SCHEDULE_PROMPTS[job.purpose].format(message=job.message),
            schedule=schedule_context(session, job, tz),
            attention=context["attention"],
            **common,
        )
    row = session.get(EventStateRow, (device_id, task.rule_id))
    attention = attention_context(task.rule_id, row.state if row else {}, task.event_status)
    resolved = task.event_status == "resolved"
    prompts = SENSOR_PROMPTS if task.rule_id == SENSOR_RULE else NOTICE_PROMPTS
    return InteractionInput(
        user_message=prompts[task.event_status].format(**attention),
        event=EventContext(
            task.event_id,
            task.event_version,
            "resolved" if resolved else "active",
            task.rule_id + ": server validated",
        ),
        attention=attention,
        approved_suggestions=() if resolved else suggestions_for(task.rule_id, attention["level"]),
        **common,
    )


def dispatch_one(sessions, interaction, timeout=30, tz="Asia/Taipei"):
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
        request = notice_request(session, task, SqlMemoryRepository(session, tz), tz)
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


def run_dispatcher(stop, sessions, interaction, timeout, tz="Asia/Taipei"):
    while not stop.is_set():
        try:
            worked = dispatch_one(sessions, interaction, timeout, tz)
        except Exception:
            # Never log model payloads, conversations, or credentials.
            logger.error("Notification dispatch failed; durable task will be retried")
            worked = False
        stop.wait(0.1 if worked else 1)
