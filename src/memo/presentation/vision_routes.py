"""The camera from the web page's side: look tasks, and MEMO's own observation.

The page is the camera: it polls for photo requests (in the background too), watches the
regions of watched tasks and the whole space, takes photos and posts them here. Photos go to
the vision model and are dropped; only records (answer, scene, quality) are kept.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, select

from memo.infrastructure.database import ObservationStateRow, VisionDailyRow, VisionObservationRow
from memo.infrastructure.observation import (
    brief_state,
    forget_device,
    situation_context,
    timeline,
)
from memo.infrastructure.vision import pending
from memo.presentation.memory_routes import Memory
from memo.presentation.vision_schemas import (
    CaptureInput,
    CaptureOutput,
    CaptureRequest,
    ObservationOutput,
    ObservationState,
    ObserveInput,
    ObserveOutput,
    PendingCaptures,
    TimelineEntry,
)

router = APIRouter(prefix="/api/v1/devices", tags=["MEMO vision"])


@router.get(
    "/{device_id}/captures/pending",
    response_model=PendingCaptures,
    summary="現在要拍的照片（拍照任務到期或需要確認）",
)
def pending_captures(device_id: str, memory: Memory):
    memory.require_device(device_id)
    return pending(memory.session, device_id, datetime.now(timezone.utc))


@router.post(
    "/{device_id}/captures",
    response_model=CaptureOutput,
    summary="送出拍照任務的照片（或沒有照片的原因），分析、比對並記錄",
)
def submit_capture(device_id: str, payload: CaptureInput, memory: Memory, request: Request):
    memory.require_device(device_id)
    memory.session.rollback()
    return request.app.state.capture_service.submit(
        device_id,
        payload.task_id,
        payload.attempt,
        image=payload.image,
        reference=payload.reference,
        quality=payload.quality.model_dump(exclude_none=True) if payload.quality else None,
        captured_at=payload.captured_at,
        skip=payload.skip,
        trigger=payload.trigger,
        moment=payload.moment,
    )


@router.post(
    "/{device_id}/schedules/{job_id}/trigger",
    response_model=CaptureRequest,
    summary="持續盯著的任務：範圍有變化並穩定了，立刻執行一次",
)
def trigger_capture(device_id: str, job_id: str, memory: Memory, request: Request):
    memory.require_device(device_id)
    memory.session.rollback()
    return request.app.state.capture_service.trigger(device_id, job_id)


@router.post(
    "/{device_id}/observe",
    response_model=ObserveOutput,
    summary="MEMO 自己觀察：送出整個畫面的照片，更新現場狀態與觀察日誌",
)
def observe(device_id: str, payload: ObserveInput, memory: Memory, request: Request):
    memory.require_device(device_id)
    memory.session.rollback()
    return request.app.state.observation_service.observe(
        device_id,
        payload.trigger,
        image=payload.image,
        quality=payload.quality.model_dump(exclude_none=True) if payload.quality else None,
        captured_at=payload.captured_at,
        skip=payload.skip,
    )


@router.get(
    "/{device_id}/observation",
    response_model=ObservationState,
    summary="MEMO 現在知道的現場、今天的觀察，以及網頁觀察的節奏",
)
def observation(device_id: str, memory: Memory, request: Request):
    memory.require_device(device_id)
    now = datetime.now(timezone.utc)
    context = situation_context(memory.session, device_id, memory.tz, now)
    row = memory.session.get(ObservationStateRow, device_id)
    return {
        "now": brief_state(row.state if row else None, memory.tz),
        "today_counts": context["today_counts"],
        **request.app.state.observation_service.config(),
    }


@router.get(
    "/{device_id}/observation/log",
    response_model=list[TimelineEntry],
    summary="觀察日誌：鏡頭看到的事、拍照任務的發現、環境事件，新的在前",
)
def observation_log(
    device_id: str,
    memory: Memory,
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=100, ge=1, le=500),
):
    memory.require_device(device_id)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return timeline(memory.session, device_id, memory.tz, since, limit)


@router.delete(
    "/{device_id}/observation/log", status_code=204, summary="清除觀察日誌與 MEMO 記得的現場"
)
def clear_observation_log(device_id: str, memory: Memory):
    memory.require_device(device_id)
    forget_device(memory.session, device_id)
    memory.session.commit()


@router.get(
    "/{device_id}/vision/observations",
    response_model=list[ObservationOutput],
    summary="拍照紀錄：答案、人事時地物與動作、照片品質（照片本身不保存）",
)
def observations(
    device_id: str,
    memory: Memory,
    job_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=200),
):
    memory.require_device(device_id)
    query = select(VisionObservationRow).where(VisionObservationRow.device_id == device_id)
    if job_id:
        query = query.where(VisionObservationRow.job_id == job_id)
    return memory.session.scalars(
        query.order_by(
            VisionObservationRow.captured_at.desc(), VisionObservationRow.created_at.desc()
        ).limit(limit)
    ).all()


@router.delete(
    "/{device_id}/vision/observations", status_code=204, summary="清除這個裝置的所有拍照紀錄"
)
def clear_observations(device_id: str, memory: Memory):
    memory.require_device(device_id)
    memory.session.execute(
        delete(VisionObservationRow).where(VisionObservationRow.device_id == device_id)
    )
    memory.session.execute(delete(VisionDailyRow).where(VisionDailyRow.device_id == device_id))
    memory.session.commit()
