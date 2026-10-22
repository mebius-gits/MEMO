"""Conversation orchestration. Task memory and dialogue remain separate ports/data."""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from memo.domain.interaction import EmotionLabel, InteractionInput
from memo.domain.schedule import parse_intent, validate_action

UNSET_ACTION = (
    "這個提醒我沒設定成功。可以換個說法，例如「10 分鐘後提醒我喝水」或「每天 9 點報環境摘要」。"
)


class MemoryRepository(Protocol):
    def context(self, device_id: str, session_id: str) -> dict: ...
    def recall(
        self, device_id: str, session_id: str, request_id: str, text: str
    ) -> dict | None: ...
    def remember(
        self, device_id: str, session_id: str, request_id: str, text: str, response: dict
    ) -> dict: ...
    def save_personality(self, device_id: str, personality) -> None: ...
    def apply_action(self, device_id: str, action: dict, session_id: str) -> str | None: ...


class CompanionService:
    def __init__(self, memory: MemoryRepository, interaction):
        self.memory = memory
        self.interaction = interaction

    def chat(self, device_id, session_id, request_id, text, personality):
        # A retried request returns the saved reply; its action must not run twice.
        saved = self.memory.recall(device_id, session_id, request_id, text)
        if saved:
            return saved
        self.memory.save_personality(device_id, personality)
        # Common phrasings are handled deterministically before the model is asked.
        actions_done = []
        parsed = parse_intent(text)
        if parsed and (done := self.memory.apply_action(device_id, parsed, session_id)):
            actions_done.append(done)
        context = self.memory.context(device_id, session_id)
        prepared = self.interaction.prepare(
            InteractionInput(
                user_message=text,
                personality=personality,
                device_id=device_id,
                context_source="stored_readings",
                actions_done=tuple(actions_done),
                **context,
            ),
            datetime.now(timezone.utc),
        )
        reply = self.interaction.respond(prepared)
        if not parsed and reply.action is not None:
            proposed = validate_action(reply.action)
            if proposed is None:
                # The text may claim success; never let an invalid action look done.
                reply = replace(reply, text=UNSET_ACTION, emotion=EmotionLabel.CONFUSED)
            elif done := self.memory.apply_action(device_id, proposed, session_id):
                actions_done.append(done)
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
            "actions": actions_done,
        }
        # Recheck after inference; never present an event that changed during generation.
        # Reminders bump the version, so compare the event itself and its lifecycle status.
        latest = self.memory.context(device_id, session_id)

        def identity(event):
            return (event.event_id, event.status) if event else None

        if identity(latest["event"]) != identity(context["event"]) and not actions_done:
            response.update(
                text="空間的狀態剛剛有變化，我會以最新消息繼續陪你留意。",
                emotion={"label": EmotionLabel.NEUTRAL.value, "intensity": 0.3},
                source="template",
                fallback_reason=None,
                event_id=None,
                event_version=None,
            )
        return self.memory.remember(device_id, session_id, request_id, text, response)
