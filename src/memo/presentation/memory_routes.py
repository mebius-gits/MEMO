from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import delete, select

from memo.domain.memory import PROFILE_ITEM_MAX, PROFILE_ITEMS, clean_profile
from memo.infrastructure.database import MemorySummaryRow, UserProfileRow
from memo.infrastructure.memory import SqlMemoryRepository
from memo.infrastructure.memory_compression import save_profile, stored_profile
from memo.presentation.memory_schemas import (
    Acknowledged,
    ConversationOutput,
    EventOutput,
    TaskOutput,
)
from memo.presentation.schemas import Identifier, Schema

router = APIRouter(prefix="/api/v1/devices", tags=["MEMO memory and events"])


def get_memory(request: Request):
    with request.app.state.session_factory() as session:
        yield SqlMemoryRepository(session, request.app.state.timezone)


Memory = Annotated[SqlMemoryRepository, Depends(get_memory)]


class Acknowledge(Schema):
    task_id: str = Field(min_length=1, max_length=64)


ProfileItem = Annotated[str, Field(min_length=1, max_length=PROFILE_ITEM_MAX)]
ProfileList = Annotated[list[ProfileItem], Field(default=[], max_length=PROFILE_ITEMS)]


class Profile(Schema):
    name: str | None = Field(default=None, max_length=20, description="怎麼稱呼使用者")
    preferences: ProfileList = Field(description="喜好與習慣")
    projects: ProfileList = Field(description="正在做的作品或事情")
    other: ProfileList = Field(description="其他值得記得的事")


class ProfileOutput(Profile):
    updated_at: datetime | None = None


class SummaryOutput(Schema):
    kind: str = Field(description="running：這次對話較早部分；episode：已結束的一次對話")
    started_at: datetime
    ended_at: datetime
    summary: str


@router.get("/{device_id}/memory/conversation", response_model=list[ConversationOutput])
def conversation(
    device_id: str,
    memory: Memory,
    session_id: Identifier = "default",
    limit: int = Query(default=20, ge=1, le=100),
):
    return memory.history(device_id, session_id, limit)


@router.delete("/{device_id}/memory/conversation", status_code=204)
def clear_conversation(device_id: str, memory: Memory, session_id: Identifier = "default"):
    memory.clear_history(device_id, session_id)


def task_output(task):
    return {
        key: getattr(task, key)
        for key in (
            "id",
            "event_id",
            "event_version",
            "rule_id",
            "event_status",
            "status",
            "created_at",
            "expires_at",
            "response",
        )
    }


@router.get("/{device_id}/memory/profile", response_model=ProfileOutput)
def profile(device_id: str, memory: Memory, session_id: Identifier = "default"):
    """MEMO 記得關於使用者的事（長期記憶），由背景的記憶壓縮從對話中整理。"""
    memory.require_device(device_id)
    row = memory.session.get(UserProfileRow, (device_id, session_id))
    return stored_profile(memory.session, device_id, session_id) | {
        "updated_at": row.updated_at if row else None
    }


@router.put("/{device_id}/memory/profile", response_model=ProfileOutput)
def correct_profile(
    device_id: str, payload: Profile, memory: Memory, session_id: Identifier = "default"
):
    """更正長期記憶；敏感內容（電話、地址、密碼等）一律不保存。"""
    memory.require_device(device_id)
    now = datetime.now(timezone.utc)
    save_profile(memory.session, device_id, session_id, clean_profile(payload.model_dump()), now)
    memory.session.commit()
    return profile(device_id, memory, session_id)


@router.delete("/{device_id}/memory/profile", status_code=204)
def forget_profile(device_id: str, memory: Memory, session_id: Identifier = "default"):
    memory.require_device(device_id)
    memory.session.execute(
        delete(UserProfileRow).where(
            UserProfileRow.device_id == device_id, UserProfileRow.session_id == session_id
        )
    )
    memory.session.commit()


@router.get("/{device_id}/memory/summaries", response_model=list[SummaryOutput])
def summaries(device_id: str, memory: Memory, session_id: Identifier = "default"):
    """壓縮後的對話記憶，舊到新。"""
    memory.require_device(device_id)
    return memory.session.scalars(
        select(MemorySummaryRow)
        .where(MemorySummaryRow.device_id == device_id, MemorySummaryRow.session_id == session_id)
        .order_by(MemorySummaryRow.last_turn_id)
    ).all()


@router.get("/{device_id}/memory/tasks", response_model=list[TaskOutput])
def task_memory(device_id: str, memory: Memory, limit: int = Query(default=30, ge=1, le=100)):
    return [task_output(t) for t in memory.tasks(device_id, limit)]


@router.get("/{device_id}/events", response_model=list[EventOutput])
def events(device_id: str, memory: Memory):
    """Each rule's current state (and the device-level sensor_offline row)."""
    # Rule state also holds engine bookkeeping (samples, streaks); expose the public fields.
    return [
        {key: value for key, value in event.items() if key in EventOutput.model_fields}
        for event in memory.events(device_id)
    ]


@router.get("/{device_id}/notifications", response_model=list[TaskOutput])
def notifications(device_id: str, memory: Memory):
    return [task_output(t) for t in memory.notifications(device_id)]


@router.post("/{device_id}/notifications/ack", response_model=Acknowledged)
def acknowledge(device_id: str, payload: Acknowledge, memory: Memory):
    return memory.acknowledge(device_id, payload.task_id)
