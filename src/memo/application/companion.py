"""Conversation orchestration. Task memory and dialogue remain separate ports/data."""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from memo.domain.interaction import EmotionLabel, InteractionInput
from memo.domain.schedule import parse_intent, validate_action

UNSET_ACTION = (
    "這個提醒我沒設定成功。可以換個說法，例如「10 分鐘後提醒我喝水」或「每天 9 點報環境摘要」。"
)
NO_LAMP = "燈光我沒辦法調，可以用網頁右側欄的「檯燈」切換。"
UNSET_SEARCH = "我沒聽懂要查什麼，可以說「查一下明天台北天氣」這樣。"


class Searcher(Protocol):
    def search(self, query: str) -> dict: ...


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
    def __init__(self, memory: MemoryRepository, interaction, searcher: Searcher | None = None):
        self.memory = memory
        self.interaction = interaction
        self.searcher = searcher

    def search(self, query: str) -> dict:
        if self.searcher is None:
            return {"query": query, "status": "not_configured", "results": [], "answers": []}
        return self.searcher.search(query)

    def chat(self, device_id, session_id, request_id, text, personality, image=None):
        # A retried request returns the saved reply; its action must not run twice.
        saved = self.memory.recall(device_id, session_id, request_id, text)
        if saved:
            return saved
        self.memory.save_personality(device_id, personality)
        # Common phrasings are handled deterministically before the model is asked.
        actions_done = []
        web_search = None
        parsed = parse_intent(text)
        if parsed and parsed["type"] == "search":
            web_search = self.search(parsed["query"])
        elif parsed and (done := self.memory.apply_action(device_id, parsed, session_id)):
            actions_done.append(done)
        context = self.memory.context(device_id, session_id)

        def ask():
            prepared = self.interaction.prepare(
                InteractionInput(
                    user_message=text,
                    personality=personality,
                    device_id=device_id,
                    context_source="stored_readings",
                    actions_done=tuple(actions_done),
                    web_search=web_search,
                    image=image,
                    **context,
                ),
                datetime.now(timezone.utc),
            )
            return prepared, self.interaction.respond(prepared)

        prepared, reply = ask()
        if not parsed and reply.action is not None:
            proposed = validate_action(reply.action)
            if proposed is None:
                # The text may claim success; never let an invalid action look done.
                unset = {"lamp": NO_LAMP, "search": UNSET_SEARCH}.get(
                    reply.action.get("type"), UNSET_ACTION
                )
                reply = replace(reply, text=unset, emotion=EmotionLabel.CONFUSED)
            elif proposed["type"] == "search":
                # The model asked to look something up: search, then answer from the results.
                # The second answer may not trigger another search or action.
                web_search = self.search(proposed["query"])
                prepared, reply = ask()
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
            # The image itself is never kept; history only notes that one was shown.
            "image_attached": bool(image),
            "sources": [
                {"title": item["title"], "url": item["url"], "source": item["source"]}
                for item in (web_search or {}).get("results", [])
            ],
        }
        # Recheck after inference; never present an event that changed during generation.
        # Reminders bump the version, so compare the event itself and its lifecycle status.
        latest = self.memory.context(device_id, session_id)

        def identity(event):
            return (event.event_id, event.status) if event else None

        changed = identity(latest["event"]) != identity(context["event"])
        # An answer about the web or a done action does not depend on the room's event.
        if changed and not actions_done and web_search is None:
            response.update(
                text="空間的狀態剛剛有變化，我會以最新消息繼續陪你留意。",
                emotion={"label": EmotionLabel.NEUTRAL.value, "intensity": 0.3},
                source="template",
                fallback_reason=None,
                event_id=None,
                event_version=None,
            )
        return self.memory.remember(device_id, session_id, request_id, text, response)
