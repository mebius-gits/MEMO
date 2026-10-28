"""Scheduled jobs (reminders, recurring tasks, follow-ups) and the welcome-back digest.

Jobs are normally created from conversation; these endpoints serve integrations such as
Node-RED, and let the web page ask what was missed while it was closed.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, Field

from memo.application.interaction import InteractionService
from memo.domain.interaction import InteractionInput
from memo.domain.schedule import following_run, validate_action
from memo.domain.vision import validate_look
from memo.infrastructure.database import ScheduledJobRow
from memo.infrastructure.schedules import job_description
from memo.infrastructure.vision import LOOK, forget
from memo.presentation.interaction_routes import get_interaction_service
from memo.presentation.interaction_schemas import EmotionOutput
from memo.presentation.memory_routes import Memory
from memo.presentation.schemas import Schema
from memo.presentation.vision_schemas import LookConfig, LookStateOutput, LookUpdate

router = APIRouter(prefix="/api/v1/devices", tags=["MEMO schedules"])

DIGEST_PROMPT = (
    "使用者剛回來。用一到兩句告訴他離開這段時間錯過了什麼（missed），"
    "再用一句說現在的狀況；不要逐條念，挑最重要的。"
)


class ScheduleCreate(Schema):
    kind: Literal["once", "interval", "daily", "followup", "event"] = Field(
        description="event：只有拍照任務能用，沒有自己的時間，只在 look.on 的感測狀況發生時看"
    )
    purpose: Literal["remind", "summary", "look"] = "remind"
    message: str = Field(default="", max_length=60, examples=["喝水"])
    look: LookConfig | None = Field(default=None, description="purpose 為 look 時必填")
    minutes: int | None = Field(
        default=None,
        ge=1,
        le=1440,
        description="once／followup：幾分鐘後；interval：間隔"
        "（至少 5 分鐘，持續盯著的拍照任務至少 1 分鐘；拍照任務不到 5 分鐘會自動改成持續盯著）",
    )
    seconds: int | None = Field(
        default=None,
        ge=5,
        le=86400,
        description="不用 minutes 時以秒計：once 至少 5 秒、followup 至少 10 秒；"
        "interval 只有拍照任務能用，至少 10 秒，並自動改成持續盯著",
    )
    time: str | None = Field(
        default=None, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$", description="daily：台北時間 HH:MM"
    )


class ScheduleUpdate(Schema):
    status: Literal["active", "paused"]


class JobOutput(Schema):
    id: str
    kind: Literal["once", "interval", "daily", "followup", "event"]
    purpose: Literal["remind", "summary", "followup", "look"]
    message: str
    minutes: int | None
    seconds: int | None = None
    time_of_day: str | None
    status: Literal["active", "paused", "done", "cancelled"]
    next_run_at: datetime | None
    runs: int
    description: str
    created_at: datetime
    look: LookConfig | None = None
    look_state: LookStateOutput | None = None


class DigestRequest(Schema):
    since: AwareDatetime = Field(description="使用者上次在場的時間")


class DigestOutput(Schema):
    text: str | None
    emotion: EmotionOutput | None = None
    missed: int
    source: Literal["template", "model"] | None = None


def job_output(job: ScheduledJobRow, tz: str) -> dict:
    fields = (
        "id",
        "kind",
        "purpose",
        "message",
        "minutes",
        "seconds",
        "time_of_day",
        "status",
        "look",
    )
    return {key: getattr(job, key) for key in fields} | {
        "next_run_at": job.next_run_at,
        "runs": job.runs,
        "created_at": job.created_at,
        "description": job_description(job, tz),
        "look_state": job.look_state,
    }


def owned_job(memory, device_id: str, job_id: str) -> ScheduledJobRow:
    memory.require_device(device_id)
    job = memory.session.get(ScheduledJobRow, job_id)
    if job is None or job.device_id != device_id:
        raise HTTPException(404, "Schedule not found")
    return job


@router.get("/{device_id}/schedules", response_model=list[JobOutput])
def list_schedules(device_id: str, memory: Memory):
    memory.require_device(device_id)
    return [job_output(job, memory.tz) for job in memory.schedules.open_jobs(device_id)]


@router.post("/{device_id}/schedules", response_model=JobOutput, status_code=201)
def create_schedule(device_id: str, payload: ScheduleCreate, memory: Memory):
    memory.require_device(device_id)
    if (payload.purpose == LOOK) != (payload.look is not None):
        raise HTTPException(422, "拍照任務（purpose look）要附上 look，其他排程不可附。")
    if payload.purpose == LOOK and payload.kind == "followup":
        raise HTTPException(422, "拍照任務只能是 once、interval 或 daily。")
    action = validate_action({"type": "schedule", **payload.model_dump(exclude_none=True)})
    if action is None:
        raise HTTPException(422, "排程參數不合法：請確認間隔、時間與提醒內容。")
    job = memory.schedules.create(device_id, action, datetime.now(timezone.utc))
    memory.session.commit()
    return job_output(job, memory.tz)


@router.patch("/{device_id}/schedules/{job_id}", response_model=JobOutput)
def update_schedule(device_id: str, job_id: str, payload: ScheduleUpdate, memory: Memory):
    job = owned_job(memory, device_id, job_id)
    if job.status not in ("active", "paused"):
        raise HTTPException(409, "Schedule already finished")
    now = datetime.now(timezone.utc)
    job.status, job.updated_at = payload.status, now
    if payload.status == "active" and job.next_run_at and job.next_run_at <= now:
        # A job resumed after its due time runs at its next slot, not immediately in a burst.
        job.next_run_at = (
            following_run(
                job.kind,
                job.minutes,
                job.time_of_day,
                job.next_run_at,
                now,
                memory.tz,
                job.seconds,
            )
            or now
        )
    memory.session.commit()
    return job_output(job, memory.tz)


@router.put("/{device_id}/schedules/{job_id}/look", response_model=JobOutput)
def update_look(device_id: str, job_id: str, payload: LookUpdate, memory: Memory):
    """Change what a look task alerts on, when it speaks, or the part of the frame it sees."""
    job = owned_job(memory, device_id, job_id)
    if job.purpose != LOOK:
        raise HTTPException(409, "Not a look task")
    if job.status not in ("active", "paused"):
        raise HTTPException(409, "Schedule already finished")
    changes = payload.model_dump(include=payload.model_fields_set)
    look = validate_look(job.look | changes)
    if look is None:
        raise HTTPException(422, "拍照任務設定不合法。")
    if look["mode"] == "change" and look.get("roi") != job.look.get("roi"):
        # A new region needs a new reference photo.
        job.look_state = (job.look_state or {}) | {"baseline_text": None, "baseline_at": None}
    job.look, job.updated_at = look, datetime.now(timezone.utc)
    memory.session.commit()
    return job_output(job, memory.tz)


@router.delete("/{device_id}/schedules/{job_id}", status_code=204)
def delete_schedule(device_id: str, job_id: str, memory: Memory):
    job = owned_job(memory, device_id, job_id)
    job.status, job.updated_at = "cancelled", datetime.now(timezone.utc)
    if job.purpose == LOOK:
        forget(memory.session, job.id)
    memory.session.commit()


@router.post("/{device_id}/digest", response_model=DigestOutput)
def digest(
    device_id: str,
    payload: DigestRequest,
    memory: Memory,
    service: InteractionService = Depends(get_interaction_service),
):
    """One or two sentences on what the user missed since ``since``; text is null if nothing."""
    missed = memory.missed(device_id, payload.since)
    if not missed:
        return DigestOutput(text=None, missed=0)
    context = memory.context(device_id, "__proactive__")
    prepared = service.prepare(
        InteractionInput(
            user_message=DIGEST_PROMPT,
            personality=memory.personality(device_id),
            device_id=device_id,
            observation=context["observation"],
            context_source="stored_readings",
            proactive=True,
            attention=context["attention"],
            jobs=context["jobs"],
            missed=missed,
            profile=memory.latest_profile(device_id),
        ),
        datetime.now(timezone.utc),
    )
    reply = service.respond(prepared)
    return DigestOutput(
        text=reply.text,
        emotion=EmotionOutput(label=reply.emotion, intensity=reply.intensity),
        missed=len(missed),
        source=reply.source,
    )
