from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime

from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Device
from memo.presentation.schemas import DeviceSchema, ReadingOutput, TelemetryInput
from memo.presentation.telemetry import to_reading

router = APIRouter(prefix="/api/v1")


def get_service(request: Request):
    with request.app.state.session_factory() as session:
        yield request.app.state.service_factory(session)


Service = Annotated[EnvironmentService, Depends(get_service)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/devices", response_model=DeviceSchema, status_code=201, tags=["devices"])
def register_device(payload: DeviceSchema, service: Service):
    return service.register_device(Device(**payload.model_dump()))


@router.get("/devices", response_model=list[DeviceSchema], tags=["devices"])
def list_devices(service: Service, limit: Limit = 100, offset: Offset = 0):
    return service.list_devices(limit, offset)


@router.get("/devices/{device_id}", response_model=DeviceSchema, tags=["devices"])
def get_device(device_id: str, service: Service):
    return service.get_device(device_id)


@router.post("/telemetry", response_model=ReadingOutput, tags=["telemetry"])
def ingest(payload: TelemetryInput, service: Service):
    return service.ingest(to_reading(payload, datetime.now(timezone.utc)))


@router.get("/devices/{device_id}/readings", response_model=list[ReadingOutput], tags=["telemetry"])
def history(
    device_id: str,
    service: Service,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
):
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be before or equal to end")
    return service.history(device_id, start, end, limit, offset)
