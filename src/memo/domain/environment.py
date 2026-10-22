"""Pure, event-time rules. Values are demonstration parameters, not safety standards."""

from dataclasses import dataclass
from uuid import uuid4


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


RULES = (
    Rule("co2_high", "co2_ppm", 1200, 1000, 120, 120),
    Rule("temperature_high", "temperature_c", 30, 28, 60, 120),
    Rule("humidity_high", "humidity_pct", 75, 70, 180, 180),
    Rule("co2_rising", "co2_ppm", 300, 100, 10, 30, window=60),
    Rule("temperature_rising", "temperature_c", 2, 1, 10, 120, window=300),
)


def advance(
    rule: Rule, previous: dict, timestamp: float, value: float | None, valid: bool
) -> tuple[dict, str | None]:
    """Return persisted state and an optional notification transition.

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
    high = score > rule.trigger if not rule.window else score >= rule.trigger
    low = score < rule.recovery
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
    return state, notice


def event_words(rule_id: str, status: str, tone: str = "gentle") -> str:
    subject = {
        "co2_high": "空氣裡的二氧化碳偏高了一陣子",
        "co2_rising": "空氣裡的二氧化碳最近上升得有點快",
        "temperature_high": "這裡偏暖已經一陣子了",
        "temperature_rising": "這裡最近暖起來得有點快",
        "humidity_high": "這裡的濕度偏高了一陣子",
    }[rule_id]
    if status == "resolved":
        recovered = {
            "co2_high": "二氧化碳已回到設定範圍，並穩定了一陣子",
            "co2_rising": "二氧化碳剛才快速上升的趨勢已經緩下來了",
            "temperature_high": "溫度已回到設定範圍，並穩定了一陣子",
            "temperature_rising": "剛才快速升溫的趨勢已經緩下來了",
            "humidity_high": "濕度已回到設定範圍，並穩定了一陣子",
        }[rule_id]
        return {
            "gentle": f"{recovered}。我會繼續陪你留意其他變化。",
            "lively": f"{recovered}！我會繼續看著這個小角落。",
            "concise": f"{recovered}，繼續觀察。",
        }[tone]
    return {
        "gentle": f"我注意到{subject}。想陪你留意一下，我會繼續看著它的變化。",
        "lively": f"欸，我發現{subject}！一起留意一下吧，我會繼續觀察。",
        "concise": f"{subject}，我會持續觀察。",
    }[tone]
