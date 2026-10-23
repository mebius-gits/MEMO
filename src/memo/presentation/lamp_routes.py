"""The desk lamp beside MEMO, switched from the web page; MEMO itself cannot change it."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from memo.infrastructure.lamp import LampRepository
from memo.infrastructure.memory import SqlMemoryRepository
from memo.presentation.schemas import Schema

router = APIRouter(prefix="/api/v1/devices", tags=["MEMO lamp"])

Mode = Literal["off", "dim", "warm", "day"]


def get_lamps(request: Request):
    with request.app.state.session_factory() as session:
        yield SqlMemoryRepository(session)


Memory = Annotated[SqlMemoryRepository, Depends(get_lamps)]


class LampChange(Schema):
    mode: Literal["off", "dim", "warm", "day", "on", "brighter", "dimmer"]


class LampOutput(Schema):
    mode: Mode
    label: str
    last_on: Mode | None
    changed_by: str | None
    updated_at: datetime | None


@router.get("/{device_id}/lamp", response_model=LampOutput)
def get_lamp(device_id: str, memory: Memory):
    """檯燈目前的模式：off 關燈、dim 暗光、warm 溫暖光、day 白天光（預設）。"""
    memory.require_device(device_id)
    return LampRepository(memory.session).get(device_id)


@router.put("/{device_id}/lamp", response_model=LampOutput)
def set_lamp(device_id: str, payload: LampChange, memory: Memory):
    """切換模式；也接受 on（回到上次的亮燈模式）、brighter、dimmer。"""
    memory.require_device(device_id)
    return LampRepository(memory.session).set(device_id, payload.mode)
