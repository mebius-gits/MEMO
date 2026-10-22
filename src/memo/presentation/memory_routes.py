from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from memo.infrastructure.memory import SqlMemoryRepository
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
        yield SqlMemoryRepository(session)


Memory = Annotated[SqlMemoryRepository, Depends(get_memory)]


class Acknowledge(Schema):
    task_id: str = Field(min_length=1, max_length=64)


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


@router.get("/{device_id}/memory/tasks", response_model=list[TaskOutput])
def task_memory(device_id: str, memory: Memory, limit: int = Query(default=30, ge=1, le=100)):
    return [task_output(t) for t in memory.tasks(device_id, limit)]


@router.get("/{device_id}/events", response_model=list[EventOutput])
def events(device_id: str, memory: Memory):
    return memory.events(device_id)


@router.get("/{device_id}/notifications", response_model=list[TaskOutput])
def notifications(device_id: str, memory: Memory):
    return [task_output(t) for t in memory.notifications(device_id)]


@router.post("/{device_id}/notifications/ack", response_model=Acknowledged)
def acknowledge(device_id: str, payload: Acknowledge, memory: Memory):
    return memory.acknowledge(device_id, payload.task_id)
