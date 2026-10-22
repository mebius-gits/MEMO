from datetime import datetime
from typing import Literal

from memo.presentation.interaction_schemas import InteractionResponse, ModelReply
from memo.presentation.schemas import Schema


class NotificationReply(ModelReply):
    source: Literal["template", "model"]
    fallback_reason: str | None


class TaskOutput(Schema):
    id: str
    event_id: str
    event_version: int
    rule_id: str
    event_status: Literal["active", "resolved"]
    status: Literal["queued", "generating", "ready", "acknowledged", "cancelled", "expired"]
    created_at: datetime
    expires_at: datetime
    response: NotificationReply | None


class ConversationOutput(Schema):
    request_id: str
    user_text: str
    response: InteractionResponse
    created_at: datetime


class EventOutput(Schema):
    rule_id: str
    status: Literal["normal", "pending", "active", "recovering", "resolved", "unknown"]
    last: float
    since: float | None = None
    live: bool = False
    score: float | None = None
    event_id: str | None = None
    version: int = 0


class Acknowledged(Schema):
    status: Literal["acknowledged"]
