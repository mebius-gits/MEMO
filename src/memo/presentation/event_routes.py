"""Event history and actions, rule thresholds, and per-device settings.

Event actions change only how MEMO speaks; whether the air is abnormal stays with the rules.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from memo.domain.environment import LEVELS
from memo.domain.interaction import Personality
from memo.domain.schedule import SNOOZE_LIMITS, validate_action
from memo.infrastructure.environment_events import default_policy, policy_for
from memo.infrastructure.event_history import EventRepository
from memo.infrastructure.rules import InvalidRule, RuleRepository
from memo.presentation.interaction_schemas import PersonalityConfig
from memo.presentation.memory_routes import Memory
from memo.presentation.schedule_routes import JobOutput, job_output
from memo.presentation.schemas import Schema

router = APIRouter(prefix="/api/v1", tags=["MEMO events, rules and settings"])

Level = Literal["caution", "warning", "danger"]
QUIET_HOURS = r"^$|^([01]?\d|2[0-3]):[0-5]\d\s*-\s*([01]?\d|2[0-3]):[0-5]\d$"


def get_events(request: Request):
    with request.app.state.session_factory() as session:
        yield EventRepository(session, request.app.state.timezone)


def get_rules(request: Request):
    with request.app.state.session_factory() as session:
        yield RuleRepository(session)


Events = Annotated[EventRepository, Depends(get_events)]
Rules = Annotated[RuleRepository, Depends(get_rules)]


# ---- events -----------------------------------------------------------------------------


class EventSummary(Schema):
    event_id: str
    rule_id: str
    status: Literal[
        "normal", "pending", "active", "recovering", "resolved", "unknown", "superseded"
    ]
    started_at: datetime
    ended_at: datetime | None
    peak_level: Level | None
    rule_version: int | None = Field(description="事件開始時採用的規則版本")
    last_update: str
    updated_at: datetime
    update_count: int


class EventUpdate(Schema):
    kind: str = Field(
        description="active / escalated / reminder / resolved / unknown / acknowledged / "
        "snoozed / handled / rule_changed / rule_disabled"
    )
    event_version: int
    level: Level | None
    value: float | None
    detail: dict | None
    at: datetime


class EventDetail(EventSummary):
    updates: list[EventUpdate]


class SnoozeRequest(Schema):
    minutes: int = Field(ge=SNOOZE_LIMITS[0], le=SNOOZE_LIMITS[1])


class HandledRequest(Schema):
    message: str = Field(
        min_length=1,
        max_length=60,
        description="使用者自述做了什麼；不是系統確認",
        examples=["我開窗了"],
    )
    minutes: int = Field(default=5, ge=1, le=120, description="幾分鐘後用新資料回報有沒有改善")


class HandledOutput(Schema):
    event: EventDetail
    followup: JobOutput


@router.get("/devices/{device_id}/events/history", response_model=list[EventSummary])
def event_history(device_id: str, events: Events, limit: int = Query(default=20, ge=1, le=100)):
    """最近的事件（新到舊），由事件更新紀錄彙總。"""
    return events.history(device_id, limit)


@router.get("/devices/{device_id}/events/{event_id}", response_model=EventDetail)
def event_detail(device_id: str, event_id: str, events: Events):
    """事件的證據與每一次變化：通知、升級、資料中斷、使用者確認／延後／處理。"""
    return events.detail(device_id, event_id)


@router.post("/devices/{device_id}/events/{event_id}/ack", response_model=EventDetail)
def acknowledge_event(device_id: str, event_id: str, events: Events):
    """使用者已知道：取消尚未送出的提醒，同等級的重複提醒延後一個間隔；等級升高仍會提醒。"""
    return events.acknowledge(device_id, event_id)


@router.post("/devices/{device_id}/events/{event_id}/snooze", response_model=EventDetail)
def snooze_event(device_id: str, event_id: str, payload: SnoozeRequest, events: Events):
    """延後重複提醒；仍持續觀察，等級升高照樣提醒。"""
    return events.snooze(device_id, event_id, payload.minutes)


@router.post(
    "/devices/{device_id}/events/{event_id}/handled",
    response_model=HandledOutput,
    status_code=201,
)
def handled_event(device_id: str, event_id: str, payload: HandledRequest, events: Events):
    """記錄使用者自述已處理，並排程在指定分鐘後比較前後數值回報。"""
    action = validate_action(
        {
            "type": "schedule",
            "kind": "followup",
            "message": payload.message,
            "minutes": payload.minutes,
        }
    )
    if action is None:
        raise HTTPException(422, "追蹤參數不合法")
    job = events.handled(device_id, event_id, action)
    return HandledOutput(
        event=events.detail(device_id, event_id), followup=job_output(job, events.tz)
    )


# ---- rules ------------------------------------------------------------------------------


class LevelThreshold(Schema):
    threshold: float
    level: Level


class RuleThresholds(Schema):
    trigger: float
    recovery: float
    hold_seconds: float = Field(ge=0, le=3600)
    recover_hold_seconds: float = Field(ge=0, le=3600)
    levels: list[LevelThreshold] = Field(default=[], max_length=len(LEVELS))


class RuleUpdate(RuleThresholds):
    enabled: bool = True


class RuleOutput(RuleThresholds):
    id: str
    metric: str
    direction: Literal["above", "below", "rising"]
    window_seconds: float | None
    enabled: bool
    version: int
    customized: bool
    updated_at: datetime | None
    defaults: RuleThresholds


@router.get("/rules", response_model=list[RuleOutput])
def list_rules(rules: Rules):
    """所有規則目前的門檻、版本與預設值；所有裝置共用。"""
    return rules.list()


@router.get("/rules/{rule_id}", response_model=RuleOutput)
def get_rule(rule_id: str, rules: Rules):
    return rules.get(rule_id)


@router.put("/rules/{rule_id}", response_model=RuleOutput)
def update_rule(rule_id: str, payload: RuleUpdate, rules: Rules):
    """更新門檻並遞增版本，API 與 worker 下一筆讀值即生效（不需重啟）。

    未確認的持續時間與趨勢窗口重新計算；進行中的事件保留原本的規則版本。
    停用規則會靜默結束進行中的事件。要還原預設值，把 `defaults` 原樣送回。"""
    override = {
        "trigger": payload.trigger,
        "recovery": payload.recovery,
        "hold": payload.hold_seconds,
        "recover_hold": payload.recover_hold_seconds,
        "levels": [(item.threshold, item.level) for item in payload.levels],
    }
    try:
        return rules.update(rule_id, payload.enabled, override)
    except InvalidRule as exc:
        raise HTTPException(422, str(exc)) from exc


# ---- settings ---------------------------------------------------------------------------


class DeviceSettings(Schema):
    personality: PersonalityConfig = PersonalityConfig()
    quiet_hours: str | None = Field(
        default=None,
        pattern=QUIET_HOURS,
        description="勿擾時段 HH:MM-HH:MM（台北時間，可跨午夜）；"
        "空字串表示不設，null 沿用伺服器預設",
        examples=["23:00-07:00"],
    )
    max_proactive_per_hour: int | None = Field(
        default=None, ge=1, le=60, description="每小時主動環境提醒上限；null 沿用伺服器預設"
    )


class EffectiveSettings(Schema):
    timezone: str
    quiet_hours: str
    max_proactive_per_hour: int


class DeviceSettingsOutput(DeviceSettings):
    effective: EffectiveSettings = Field(
        description="實際生效的值（裝置設定優先，否則用伺服器預設）"
    )


def settings_output(memory, device_id) -> DeviceSettingsOutput:
    preferences = memory.preferences(device_id)
    personality = memory.personality(device_id)
    policy = policy_for(memory.session, device_id)
    quiet = (
        f"{policy.quiet_start:%H:%M}-{policy.quiet_end:%H:%M}"
        if policy.quiet_start is not None and policy.quiet_end is not None
        else ""
    )
    return DeviceSettingsOutput(
        personality=PersonalityConfig(
            name=personality.name,
            language=personality.language,
            tone=personality.tone,
            persona=personality.persona,
        ),
        quiet_hours=preferences.get("quiet_hours"),
        max_proactive_per_hour=preferences.get("max_proactive_per_hour"),
        effective=EffectiveSettings(
            timezone=default_policy().timezone,
            quiet_hours=quiet,
            max_proactive_per_hour=policy.max_per_hour,
        ),
    )


@router.get("/devices/{device_id}/settings", response_model=DeviceSettingsOutput)
def get_settings(device_id: str, memory: Memory):
    """人格、勿擾時段與每小時提醒上限。人格也會被最後一次對話使用的人格更新。"""
    memory.require_device(device_id)
    return settings_output(memory, device_id)


@router.put("/devices/{device_id}/settings", response_model=DeviceSettingsOutput)
def put_settings(device_id: str, payload: DeviceSettings, memory: Memory):
    """API 與 worker 下一次判斷即採用，不需重啟。"""
    personality = Personality(**payload.personality.model_dump())
    memory.save_settings(
        device_id,
        personality,
        {
            "quiet_hours": payload.quiet_hours,
            "max_proactive_per_hour": payload.max_proactive_per_hour,
        },
    )
    return settings_output(memory, device_id)
