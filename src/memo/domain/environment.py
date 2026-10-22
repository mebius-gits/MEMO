"""Pure, event-time rules. Thresholds follow indoor-comfort guidance; not safety standards."""

from dataclasses import dataclass
from uuid import uuid4

LEVELS = ("caution", "warning", "danger")
LEVEL_LABELS = {"caution": "注意", "warning": "警戒", "danger": "危險"}
# While an event stays active MEMO speaks again after this many seconds, by severity.
REPEAT_SECONDS = {"caution": 1800, "warning": 900, "danger": 300}
# Minimum gap before an escalation notice, so a value hovering on a boundary cannot flap.
ESCALATION_GAP = 120


@dataclass(frozen=True)
class Rule:
    id: str
    metric: str
    trigger: float
    recovery: float
    hold: float
    recover_hold: float
    window: float = 0
    max_gap: float = 15
    # "below" rules fire when the value drops under trigger and recover above recovery.
    direction: str = "above"
    # (threshold, level) from mild to severe; the score reaching a threshold sets the level.
    levels: tuple[tuple[float, str], ...] = ()


RULES = (
    Rule(
        "co2_high",
        "co2_ppm",
        1200,
        1000,
        120,
        120,
        levels=((1200, "caution"), (1500, "warning"), (2000, "danger")),
    ),
    # Uses the feels-like temperature (heat index), so 34 °C at 60 % counts as about 42 °C.
    Rule(
        "temperature_high",
        "feels_like_c",
        30,
        28,
        60,
        120,
        levels=((30, "caution"), (35, "warning"), (41, "danger")),
    ),
    # Comfortable indoor humidity is roughly 40-60 %: above 70 % favours mould and mites,
    # below 35 % (humidity_low) dries skin and airways.
    Rule(
        "humidity_high",
        "humidity_pct",
        70,
        65,
        180,
        180,
        levels=((70, "caution"), (80, "warning")),
    ),
    Rule("co2_rising", "co2_ppm", 300, 100, 10, 30, window=60),
    Rule("temperature_rising", "temperature_c", 2, 1, 10, 120, window=300),
    Rule(
        "humidity_low",
        "humidity_pct",
        35,
        40,
        180,
        180,
        direction="below",
        levels=((35, "caution"), (25, "warning")),
    ),
    Rule(
        "temperature_low",
        "temperature_c",
        18,
        20,
        120,
        120,
        direction="below",
        levels=((18, "caution"), (15, "warning"), (12, "danger")),
    ),
)
RULES_BY_ID = {rule.id: rule for rule in RULES}


def level_of(rule: Rule, score: float) -> str:
    level = LEVELS[0]
    for threshold, name in rule.levels:
        if (score <= threshold) if rule.direction == "below" else (score >= threshold):
            level = name
    return level


def advance(
    rule: Rule, previous: dict, timestamp: float, value: float | None, valid: bool
) -> tuple[dict, str | None]:
    """Return persisted state and an optional notice.

    Notices: "active" and "resolved" for the event lifecycle; while active, "escalated" when
    the severity level rises and "reminder" when the level's repeat interval has passed.
    Gaps interrupt holds. Unknown never implies recovery. Old samples cannot rewind state.
    """
    state = dict(previous)
    last = state.get("last")
    if last is not None and timestamp <= last:
        return state, None
    status = state.get("status", "normal")
    live = state.get("live", False)
    gap = last is not None and timestamp - last > rule.max_gap
    state["last"] = timestamp
    if not valid or value is None or gap:
        state.update(since=None, samples=[])
        state["status"] = "unknown" if live else "normal"
        if not valid or value is None:
            return state, None
        status = state["status"]

    samples = [s for s in state.get("samples", []) if s[0] >= timestamp - rule.window]
    # Bound storage without losing coverage for high-frequency devices.
    if not samples or timestamp - samples[-1][0] >= max(1, rule.window / 120):
        samples.append([timestamp, value])
    state["samples"] = samples[-128:] if rule.window else []
    score = value
    if rule.window:
        # Require a covered window, at least three samples, and no invalid gaps.
        if len(samples) < 3 or timestamp - samples[0][0] < rule.window - rule.max_gap:
            state.update(status="unknown" if live else "normal", since=None)
            return state, None
        score = value - samples[0][1]
    state["score"] = score
    if rule.window:
        high, low = score >= rule.trigger, score < rule.recovery
    elif rule.direction == "below":
        high, low = score < rule.trigger, score > rule.recovery
    else:
        high, low = score > rule.trigger, score < rule.recovery
    notice = None
    if not live:
        if high:
            if status != "pending":
                state["since"] = timestamp
            state["status"] = "pending"
            if timestamp - state["since"] >= rule.hold:
                state.update(status="active", live=True, event_id=uuid4().hex, since=None)
                notice = "active"
        else:
            state.update(status="normal", since=None)
    elif low:
        if status != "recovering":
            state["since"] = timestamp
        state["status"] = "recovering"
        if timestamp - state["since"] >= rule.recover_hold:
            state.update(status="resolved", live=False, since=None)
            notice = "resolved"
    else:
        state.update(status="active", since=None)
    # Trends are momentary; only level rules keep reminding while they stay active.
    if rule.window:
        return state, notice
    return state, attend(rule, state, notice, timestamp, score)


def attend(rule: Rule, state: dict, notice: str | None, timestamp: float, score: float):
    if notice == "active":
        level = level_of(rule, score)
        state.update(
            level=level,
            active_since=timestamp,
            notified_at=timestamp,
            notified_level=level,
            reminders=0,
            snoozed_until=0,
        )
        return notice
    if notice == "resolved":
        state.update(level=None, notified_level=None)
        return notice
    if not state.get("live") or state.get("status") != "active":
        return notice
    level = level_of(rule, score)
    state["level"] = level
    # States created before reminders existed start their clocks now.
    state.setdefault("active_since", timestamp)
    state.setdefault("notified_at", timestamp)
    notified = state.get("notified_level") or level
    since_notice = timestamp - state["notified_at"]
    if LEVELS.index(level) > LEVELS.index(notified) and since_notice >= ESCALATION_GAP:
        # Escalation breaks through a snooze: it is new information, not a repeat.
        state.update(notified_at=timestamp, notified_level=level)
        return "escalated"
    if LEVELS.index(level) < LEVELS.index(notified):
        # Step down quietly so a later rise escalates again.
        state["notified_level"] = level
    elif since_notice >= REPEAT_SECONDS[level] and timestamp >= state.get("snoozed_until", 0):
        state.update(notified_at=timestamp, reminders=state.get("reminders", 0) + 1)
        return "reminder"
    return notice


SUBJECTS = {
    "co2_high": "空氣裡的二氧化碳偏高了一陣子",
    "co2_rising": "空氣裡的二氧化碳最近上升得有點快",
    "temperature_high": "這裡偏熱已經一陣子了",
    "temperature_rising": "這裡最近暖起來得有點快",
    "humidity_high": "這裡的濕度偏高了一陣子",
    "humidity_low": "這裡的空氣偏乾了一陣子",
    "temperature_low": "這裡有點冷已經一陣子了",
}
RECOVERED = {
    "co2_high": "二氧化碳已回到設定範圍，並穩定了一陣子",
    "co2_rising": "二氧化碳剛才快速上升的趨勢已經緩下來了",
    "temperature_high": "溫度已回到設定範圍，並穩定了一陣子",
    "temperature_rising": "剛才快速升溫的趨勢已經緩下來了",
    "humidity_high": "濕度已回到舒適範圍，並穩定了一陣子",
    "humidity_low": "濕度已回到舒適範圍，並穩定了一陣子",
    "temperature_low": "溫度已回暖到舒適範圍，並穩定了一陣子",
}
# Server-approved suggestions the model may use; nothing outside these is recommended.
SUGGESTIONS = {
    "temperature_high": {
        "caution": ("補充水分",),
        "warning": ("補充水分", "開電扇或冷氣降溫"),
        "danger": ("補充水分", "開電扇或冷氣降溫", "暫停費力活動，若頭暈不適請休息並找人協助"),
    },
    "co2_high": {"*": ("讓空氣流通，例如開啟換氣扇", "起來走動一下")},
    "humidity_high": {"*": ("開除濕機",)},
    "humidity_low": {"*": ("使用加濕器", "多喝水")},
    "temperature_low": {"*": ("加件衣服", "喝點溫熱的飲料")},
}


def suggestions_for(rule_id: str, level: str | None) -> tuple[str, ...]:
    options = SUGGESTIONS.get(rule_id, {})
    return options.get(level or "", options.get("*", ()))


def event_words(
    rule_id: str,
    status: str,
    tone: str = "gentle",
    *,
    kind: str | None = None,
    level: str | None = None,
    minutes: int | None = None,
) -> str:
    if status == "resolved":
        recovered = RECOVERED[rule_id]
        return {
            "gentle": f"{recovered}。我會繼續陪你留意其他變化。",
            "lively": f"{recovered}！我會繼續看著這個小角落。",
            "concise": f"{recovered}，繼續觀察。",
        }[tone]
    subject = SUBJECTS[rule_id]
    if kind == "escalated" and level:
        subject += f"，而且已經到了「{LEVEL_LABELS[level]}」的程度"
    elif kind == "reminder" and minutes:
        subject += f"，已經持續大約 {minutes} 分鐘了"
    return {
        "gentle": f"我注意到{subject}。想陪你留意一下，我會繼續看著它的變化。",
        "lively": f"欸，我發現{subject}！一起留意一下吧，我會繼續觀察。",
        "concise": f"{subject}，我會持續觀察。",
    }[tone]
