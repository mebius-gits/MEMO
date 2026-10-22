from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Tone(str, Enum):
    GENTLE = "gentle"
    LIVELY = "lively"
    CONCISE = "concise"


class EmotionLabel(str, Enum):
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    CONCERNED = "concerned"
    HAPPY = "happy"
    CONFUSED = "confused"


@dataclass(frozen=True)
class Personality:
    name: str = "MEMO"
    language: str = "zh-TW"
    tone: Tone = Tone.GENTLE
    persona: str = ""


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    metrics: dict[str, float | None]
    quality: str


@dataclass(frozen=True)
class EventContext:
    event_id: str
    event_version: int
    status: str
    rule_evidence: str


@dataclass(frozen=True)
class InteractionInput:
    user_message: str
    personality: Personality
    observation: Observation | None = None
    event: EventContext | None = None
    device_id: str | None = None
    approved_suggestions: tuple[str, ...] = ()
    history: tuple[dict[str, str], ...] = ()
    context_source: str = "caller_supplied"
    task_memory: tuple[dict, ...] = ()
    proactive: bool = False
    # Rule-engine view of the current situation: severity, how long, reminders so far.
    attention: dict | None = None
    # The scheduled job being delivered (remind / summary / follow-up with before & after).
    schedule: dict | None = None
    # Active scheduled jobs, described in Chinese, so MEMO can answer "what reminders do I have".
    jobs: tuple[str, ...] = ()
    # Actions the backend just carried out for this message (created, snoozed, cancelled...).
    actions_done: tuple[str, ...] = ()
    # Notices the user did not see while away, for the welcome-back digest.
    missed: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PreparedInteraction:
    input: InteractionInput
    system_prompt: str
    user_prompt: str
    warnings: tuple[str, ...] = ()
    usable_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    text: str
    emotion: EmotionLabel
    intensity: float
    source: str = "template"
    fallback_reason: str | None = "model_not_configured"
    # An optional action the model proposes; the backend validates it before doing anything.
    action: dict | None = None
