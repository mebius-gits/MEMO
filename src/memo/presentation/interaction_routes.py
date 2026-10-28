from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from memo.application.companion import CompanionService
from memo.application.interaction import InteractionService
from memo.domain.interaction import EventContext, InteractionInput, Observation, Personality
from memo.presentation.interaction_schemas import (
    DeviceInteractionRequest,
    EmotionOutput,
    InteractionRequest,
    InteractionResponse,
    ModelReply,
    PersonalityConfig,
    PreparedResponse,
)
from memo.presentation.memory_routes import Memory

router = APIRouter(prefix="/api/v1/llm", tags=["MEMO interaction"])
device_router = APIRouter(prefix="/api/v1/devices", tags=["MEMO interaction"])


def get_interaction_service(request: Request) -> InteractionService:
    return request.app.state.interaction_service


def to_input(payload: InteractionRequest) -> InteractionInput:
    observation = payload.context.observation
    event = payload.context.event
    return InteractionInput(
        user_message=payload.user_message,
        personality=Personality(**payload.personality.model_dump()),
        observation=Observation(
            observation.observed_at, observation.metrics.usable(), observation.quality
        )
        if observation
        else None,
        event=EventContext(**event.model_dump()) if event else None,
        device_id=payload.context.device_id,
        approved_suggestions=tuple(payload.context.approved_suggestions),
        history=tuple(item.model_dump() for item in payload.history),
    )


@router.get("/personality", response_model=PersonalityConfig, summary="取得 MEMO 預設人格")
def get_personality():
    return PersonalityConfig()


@device_router.post(
    "/{device_id}/interaction",
    response_model=InteractionResponse,
    summary="從後端資料庫取得裝置最新讀值，產生 MEMO 回覆",
)
def device_interaction(
    device_id: str,
    payload: DeviceInteractionRequest,
    memory: Memory,
    request: Request,
    service: InteractionService = Depends(get_interaction_service),
):
    return CompanionService(memory, service, request.app.state.searcher).chat(
        device_id,
        payload.session_id,
        str(payload.request_id),
        payload.user_message,
        Personality(**payload.personality.model_dump()),
        payload.image,
        payload.camera,
    )


@router.post(
    "/prepare", response_model=PreparedResponse, summary="組裝 LLM 輸入與輸出契約（不呼叫模型）"
)
def prepare(
    payload: InteractionRequest,
    service: InteractionService = Depends(get_interaction_service),
):
    prepared = service.prepare(to_input(payload), datetime.now(timezone.utc))
    return PreparedResponse(
        request_id=payload.request_id,
        system_prompt=prepared.system_prompt,
        user_prompt=prepared.user_prompt,
        output_schema=ModelReply.model_json_schema(),
        warnings=list(prepared.warnings),
    )


@router.post(
    "/respond", response_model=InteractionResponse, summary="產生 MEMO 文字與情緒（模型或備援回覆）"
)
def respond(
    payload: InteractionRequest,
    service: InteractionService = Depends(get_interaction_service),
):
    prepared = service.prepare(to_input(payload), datetime.now(timezone.utc))
    reply = service.respond(prepared)
    event = payload.context.event
    return InteractionResponse(
        request_id=payload.request_id,
        source=reply.source,
        fallback_reason=reply.fallback_reason,
        text=reply.text,
        emotion=EmotionOutput(label=reply.emotion, intensity=reply.intensity),
        event_id=event.event_id if event else None,
        event_version=event.event_version if event else None,
        warnings=list(prepared.warnings),
    )
