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
    # Environment notices: active / escalated / reminder / resolved.
    # Scheduled jobs (rule_id "schedule"): remind / summary / followup.
    event_status: Literal[
        "active", "escalated", "reminder", "resolved", "remind", "summary", "followup"
    ]
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
    level: Literal["caution", "warning", "danger"] | None = None
    active_since: float | None = None
    notified_at: float | None = None
    notified_level: Literal["caution", "warning", "danger"] | None = None
    reminders: int = 0
    snoozed_until: float = 0
    acknowledged_at: float | None = None
    rule_version: int | None = None


class Acknowledged(Schema):
    status: Literal["acknowledged"]
