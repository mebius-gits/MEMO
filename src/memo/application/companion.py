"""Conversation orchestration. Task memory and dialogue remain separate ports/data."""

from datetime import datetime, timezone
from typing import Protocol

from memo.domain.interaction import InteractionInput


class MemoryRepository(Protocol):
    def context(self, device_id: str, session_id: str) -> dict: ...
    def recall(
        self, device_id: str, session_id: str, request_id: str, text: str
    ) -> dict | None: ...
    def remember(
        self, device_id: str, session_id: str, request_id: str, text: str, response: dict
    ) -> dict: ...


class CompanionService:
    def __init__(self, memory: MemoryRepository, interaction):
        self.memory = memory
        self.interaction = interaction

    def chat(self, device_id, session_id, request_id, text, personality):
        context = self.memory.context(device_id, session_id)
        saved = self.memory.recall(device_id, session_id, request_id, text)
        if saved:
            return saved
        prepared = self.interaction.prepare(
            InteractionInput(
                user_message=text,
                personality=personality,
                device_id=device_id,
                context_source="stored_readings",
                **context,
            ),
            datetime.now(timezone.utc),
        )
        reply = self.interaction.respond(prepared)
        response = {
            "request_id": request_id,
            "character": "MEMO",
            "text": reply.text,
            "emotion": {"label": reply.emotion.value, "intensity": reply.intensity},
            "source": reply.source,
            "fallback_reason": reply.fallback_reason,
            "context_source": "stored_readings",
            "warnings": list(prepared.warnings),
            "event_id": context["event"].event_id if context["event"] else None,
            "event_version": context["event"].event_version if context["event"] else None,
        }
        # Recheck after inference; never present an event that changed during generation.
        latest = self.memory.context(device_id, session_id)
        if latest["event"] != context["event"]:
            from memo.domain.interaction import EmotionLabel

            response.update(
                text="空間的狀態剛剛有變化，我會以最新消息繼續陪你留意。",
                emotion={"label": EmotionLabel.NEUTRAL.value, "intensity": 0.3},
                source="template",
                fallback_reason=None,
                event_id=None,
                event_version=None,
            )
        return self.memory.remember(device_id, session_id, request_id, text, response)
