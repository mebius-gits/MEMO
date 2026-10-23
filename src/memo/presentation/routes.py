from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime

from memo.application.use_cases import EnvironmentService
from memo.domain.comfort import feels_like_c
from memo.domain.entities import Device
from memo.domain.environment import STALE_SECONDS
from memo.presentation.schemas import (
    DeviceSchema,
    DeviceState,
    ReadingBucket,
    ReadingOutput,
    TelemetryAccepted,
    TelemetryInput,
)
from memo.presentation.telemetry import to_reading

router = APIRouter(prefix="/api/v1")


def get_service(request: Request):
    with request.app.state.session_factory() as session:
        yield request.app.state.service_factory(session)


Service = Annotated[EnvironmentService, Depends(get_service)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
SUMMARY_RANGE = timedelta(days=7)
MAX_BUCKETS = 2000


@router.post("/devices", response_model=DeviceSchema, status_code=201, tags=["devices"])
def register_device(payload: DeviceSchema, service: Service):
    return service.register_device(Device(**payload.model_dump()))


@router.get("/devices", response_model=list[DeviceSchema], tags=["devices"])
def list_devices(service: Service, limit: Limit = 100, offset: Offset = 0):
    return service.list_devices(limit, offset)


@router.get("/devices/{device_id}", response_model=DeviceSchema, tags=["devices"])
def get_device(device_id: str, service: Service):
    return service.get_device(device_id)


@router.post("/telemetry", response_model=TelemetryAccepted, tags=["telemetry"])
def ingest(payload: TelemetryInput, service: Service):
    reading = service.ingest(to_reading(payload, datetime.now(timezone.utc)))
    return TelemetryAccepted(
        **ReadingOutput.model_validate(reading).model_dump(),
        rejected_metrics=payload.metrics.rejected(),
    )


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


@router.get("/devices/{device_id}/state", response_model=DeviceState, tags=["telemetry"])
def latest_state(device_id: str, service: Service):
    """每個指標各自最新的有效值與新鮮度；指標可能來自不同時間的讀值。"""
    now = datetime.now(timezone.utc)
    metrics = {}
    for metric, latest in service.latest_state(device_id).items():
        if latest is None:
            metrics[metric] = None
            continue
        value, observed_at = latest
        age = (now - observed_at).total_seconds()
        metrics[metric] = {
            "value": value,
            "observed_at": observed_at,
            "age_seconds": round(age, 1),
            "fresh": 0 <= age <= STALE_SECONDS,
        }
    fresh = {key: m["value"] for key, m in metrics.items() if m and m["fresh"]}
    return DeviceState(
        device_id=device_id,
        checked_at=now,
        online=bool(fresh),
        metrics=metrics,
        feels_like_c=feels_like_c(fresh.get("temperature_c"), fresh.get("humidity_pct")),
    )


@router.get(
    "/devices/{device_id}/readings/summary",
    response_model=list[ReadingBucket],
    tags=["telemetry"],
)
def summary(
    device_id: str,
    service: Service,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    bucket_seconds: Annotated[int, Query(ge=10, le=86400)] = 60,
):
    """依時間區間彙總有效讀值（平均、最小、最大）；預設最近一小時、每分鐘一格，沒資料的格子不列出。"""
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(422, "start must be before end")
    if end - start > SUMMARY_RANGE:
        raise HTTPException(422, "range must be at most 7 days")
    if (end - start).total_seconds() / bucket_seconds > MAX_BUCKETS:
        raise HTTPException(422, f"at most {MAX_BUCKETS} buckets; use a larger bucket_seconds")
    return service.summary(device_id, start, end, bucket_seconds)
