"""Durable task dispatcher. Model latency never blocks telemetry acknowledgement."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from memo.domain.environment import SENSOR_RULE, suggestions_for
from memo.domain.interaction import EventContext, InteractionInput
from memo.infrastructure.database import DeviceRow, EventStateRow, ScheduledJobRow, TaskMemoryRow
from memo.infrastructure.environment_events import (
    OBSERVE_RULE,
    SCHEDULE_RULE,
    attention_context,
    current_task,
)
from memo.infrastructure.memory import SqlMemoryRepository
from memo.infrastructure.observation import notice_context as observed_context
from memo.infrastructure.schedules import schedule_context
from memo.infrastructure.vision import LOOK, notice_context

logger = logging.getLogger(__name__)

# What MEMO is asked to do for each kind of notice; the context JSON carries the facts.
NOTICE_PROMPTS = {
    "active": (
        "請用你的人格主動提醒使用者剛觀察到的狀況（見 attention），一到兩句。"
        "situation.now 有剛看到的畫面時，把畫面和數值合起來說一句（例如 CO₂ 在升，"
        "桌前坐了兩個人）；兩邊對不上就照實說（CO₂ 在升，但畫面裡沒看到人），不要猜原因。"
    ),
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


# Look notices: what the photo showed is in schedule; the level sets how direct MEMO is.
LOOK_PROMPTS = {
    "alert": (
        "你剛照使用者的要求拍照確認「{question}」，結果是需要提醒的情況（見 schedule 的 detail"
        " 與 scene）。用你的人格輕輕提醒他，一到兩句，不要說教。"
    ),
    "repeat": (
        "「{question}」的情況已經連續 {streak} 次了，今天第 {today_count} 次，"
        "這是第 {level} 級提醒。level 2 說得直接一點並提到今天的次數；"
        "level 3 先提醒，再問他要不要先暫停這個拍照任務。"
        "換個說法，不要重複 recent_notices 裡說過的話。"
    ),
    "recovered": "上次提醒的「{question}」已經改善了（見 schedule），用一句話肯定他。",
    "changed": (
        "你拍照比對發現「{question}」有變化（見 schedule 的 detail 與 scene），"
        "用一兩句告訴使用者哪裡變了，不要猜原因。schedule 有 sensors 或 triggered_by 時，"
        "把畫面和感測合成一句（例如「燈剛被打開，桌前有人坐下，CO₂ 也在升」）；"
        "畫面和感測對不上時照實說兩邊，不要硬湊原因。"
    ),
    "report": (
        "這是使用者要你定時拍照回報的「{question}」，用一兩句說你看到的結果"
        "（schedule 的 result、detail 與 scene），result 和 previous 不同時說出變化。"
    ),
    "scene": (
        "這是使用者要你定時回報的「{question}」。先用一句說畫面（schedule 的 detail 與 scene），"
        "和 previous_detail 比有明顯不同就說出來；再用一句說空氣、溫濕度或光線"
        "（usable_metrics 與 attention），沒狀況就簡短帶過。不要從畫面猜溫度或空氣。"
    ),
    "unseen": (
        "你連續幾次拍照都看不清楚「{question}」（見 schedule 的 quality），"
        "用一句話請使用者確認鏡頭、角度或光線。"
    ),
}


# Notices from MEMO's own observation; schedule carries the event and the situation.
OBSERVE_PROMPTS = {
    "arrival": (
        "鏡頭看到有人回到這裡了（離開約 {away_minutes} 分鐘）。用你的個性打個招呼，一到兩句；"
        "while_away 裡有值得說的事就挑一件說，空氣或溫濕度有狀況（attention）也提一句。"
    ),
    "animal": "鏡頭看到{detail}。用一句話告訴使用者，口吻輕鬆，可以有你的小邏輯，不要猜原因。",
}


def notice_request(session, task, memory, tz, look_timeout=None):
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
        # Who is here, as far as the camera knows: a notice is for the people present.
        situation=context.get("situation"),
    )
    if task.rule_id == OBSERVE_RULE:
        schedule = observed_context(session, task, tz)
        prompt = OBSERVE_PROMPTS.get(schedule["notice"], OBSERVE_PROMPTS["animal"])
        return InteractionInput(
            user_message=prompt.format(**({"away_minutes": "一段時間", "detail": ""} | schedule)),
            schedule=schedule,
            attention=context["attention"],
            timeout_seconds=look_timeout,
            **common,
        )
    if task.rule_id == SCHEDULE_RULE and task.event_status == LOOK:
        job = session.get(ScheduledJobRow, task.event_id)
        schedule = notice_context(session, job, task, tz, datetime.now(timezone.utc))
        notice = (
            "scene" if schedule.get("mode") == "report" and schedule["notice"] == "report" else None
        )
        prompt = LOOK_PROMPTS.get(notice or schedule["notice"], LOOK_PROMPTS["report"])
        return InteractionInput(
            user_message=prompt.format(**schedule),
            schedule=schedule,
            timeout_seconds=look_timeout,
            **common,
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


def dispatch_one(sessions, interaction, timeout=30, tz="Asia/Taipei", look_timeout=None):
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
        request = notice_request(session, task, SqlMemoryRepository(session, tz), tz, look_timeout)
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


def run_dispatcher(stop, sessions, interaction, timeout, tz="Asia/Taipei", look_timeout=None):
    while not stop.is_set():
        try:
            worked = dispatch_one(sessions, interaction, timeout, tz, look_timeout)
        except Exception:
            # Never log model payloads, conversations, or credentials.
            logger.error("Notification dispatch failed; durable task will be retried")
            worked = False
        stop.wait(0.1 if worked else 1)
