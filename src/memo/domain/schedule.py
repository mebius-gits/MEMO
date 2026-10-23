"""Reminders, recurring tasks, follow-ups and snoozes: parsing, validation and timing.

Pure functions. Actions come from two places and share one schema, so both are validated
the same way: the deterministic Chinese phrase parser below, and a model's optional "action".

    {"type": "schedule", "kind": "once|interval|daily|followup", "purpose": "remind|summary",
     "message": "喝水", "minutes": 10, "time": "09:00"}
    {"type": "snooze", "minutes": 30}
    {"type": "cancel" | "pause" | "resume", "match": "喝水"}   # empty match = all
    {"type": "search", "query": "明天台北天氣"}
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from memo.domain.web import parse_search, validate_search

KINDS = ("once", "interval", "daily", "followup")
MINUTE_LIMITS = {"once": (1, 1440), "interval": (5, 1440), "followup": (1, 120)}
SNOOZE_LIMITS = (5, 240)
MESSAGE_MAX = 60
FOLLOWUP_DEFAULT = 5
SNOOZE_DEFAULT = 30


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def validate_action(raw) -> dict | None:
    """Normalise a proposed action; None when it is malformed or out of bounds."""
    if not isinstance(raw, dict):
        return None
    kind_of = raw.get("type")
    if kind_of == "schedule":
        kind = raw.get("kind")
        if kind not in KINDS:
            return None
        message = str(raw.get("message") or "").strip()[:MESSAGE_MAX]
        purpose = "followup" if kind == "followup" else raw.get("purpose")
        if purpose not in ("remind", "summary", "followup"):
            purpose = "remind"
        if purpose in ("remind", "followup") and not message:
            return None
        action = {"type": "schedule", "kind": kind, "purpose": purpose, "message": message}
        if kind == "daily":
            match = re.fullmatch(
                r"([01]?\d|2[0-3])[:：]([0-5]\d)", str(raw.get("time", "")).strip()
            )
            if not match:
                return None
            action["time"] = f"{int(match[1]):02d}:{match[2]}"
        else:
            default = FOLLOWUP_DEFAULT if kind == "followup" else None
            minutes = _int(raw.get("minutes", default))
            low, high = MINUTE_LIMITS[kind]
            if minutes is None or not low <= minutes <= high:
                return None
            action["minutes"] = minutes
        return action
    if kind_of == "snooze":
        minutes = _int(raw.get("minutes", SNOOZE_DEFAULT))
        if minutes is None or not SNOOZE_LIMITS[0] <= minutes <= SNOOZE_LIMITS[1]:
            return None
        return {"type": "snooze", "minutes": minutes}
    if kind_of in ("cancel", "pause", "resume"):
        match = str(raw.get("match") or "").strip()[:30]
        return {"type": kind_of, "match": "" if match in ("所有", "全部") else match}
    if kind_of == "search":
        return validate_search(raw)
    return None


# ---- Chinese phrase parser -------------------------------------------------------------

_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5}
_DIGITS |= {"六": 6, "七": 7, "八": 8, "九": 9}
NUM = r"[0-9零〇一二兩两三四五六七八九十百]+"
UNIT = r"(?P<unit>分鐘|分|個?小時|個?鐘頭)"
_WIDE = str.maketrans("０１２３４５６７８９：", "0123456789:")


def chinese_number(text: str | None) -> int | None:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = current = 0
    for char in text:
        if char in _DIGITS:
            current = _DIGITS[char]
        elif char == "十":
            total, current = total + (current or 1) * 10, 0
        elif char == "百":
            total, current = total + (current or 1) * 100, 0
        else:
            return None
    return total + current


def _minutes(number: str | None, unit: str) -> int | None:
    per = 60 if unit.endswith(("小時", "鐘頭")) else 1
    if number == "半":
        return per // 2 if per == 60 else None
    value = 1 if number is None else chinese_number(number)
    return None if value is None else value * per


def _message(text: str) -> str:
    text = re.sub(r"^(?:要|去|該|記得|一下|跟我說|告訴我|說|報)+", "", text)
    return re.sub(r"[。！？!?,，～~啦喔哦吧呀了]+$", "", text).strip()


_CANCEL = re.compile(r"(?:取消|刪除|刪掉|停止|關掉)(?P<what>.*?)的?提醒")
_STOP = re.compile(r"(?:不用|不要|別)再?提醒我(?P<what>.+?)了?$")
_PAUSE = re.compile(r"暫停(?:所有|全部)?(?P<what>.*?)的?提醒")
_RESUME = re.compile(r"(?:恢復|繼續)(?:所有|全部)?(?P<what>.*?)的?提醒")
_DAILY = re.compile(
    rf"每天(?P<period>早上|上午|中午|下午|傍晚|晚上)?(?P<hour>{NUM})(?:點|:)"
    rf"(?P<minute>半|{NUM})?分?(?:的時候)?(?P<rest>.*)"
)
# The message may not start with 我, so "…後再提醒我" (no object) stays a snooze.
_INTERVAL = re.compile(rf"每(?:隔)?(?P<n>{NUM}|半)?{UNIT}(?:就)?(?:提醒|叫)我?(?P<msg>[^我].*)")
_ONCE = re.compile(
    rf"(?P<n>{NUM}|半)?{UNIT}(?:以?後|之後)(?:再)?(?:提醒|叫|跟我說)我?(?P<msg>[^我].*)"
)
_ONCE_LATER = re.compile(rf"提醒我(?P<n>{NUM}|半)?{UNIT}(?:以?後|之後)(?:要)?(?P<msg>[^我].*)")
_SNOOZE_FOR = re.compile(rf"(?P<n>{NUM}|半)?{UNIT}(?:以?後|之後)再(?:提醒|說|叫)我?$")
_FOLLOWUP = re.compile(
    r"(?P<act>(?:開了?|打開了?)(?:窗戶?|冷氣|電扇|風扇|除濕機|空調|加濕器|暖氣|換氣扇)"
    r"|關了?(?:窗戶?|暖氣|冷氣))"
)
_SNOOZE = re.compile(
    r"(?:知道了|收到了?|晚點再說|等等再說|等一下再說|先不要提醒|(?:別|不要)再提醒我?了|"
    r"不要一直提醒|好啦好啦)"
)


def parse_intent(text: str) -> dict | None:
    """Recognise common task phrasings; returns a validated action or None."""
    # Search queries keep their spaces ("Python 3.14"); everything else matches without them.
    spaced = re.sub(r"\s+", " ", text.translate(_WIDE)).strip()
    text = re.sub(r"\s+", "", text.translate(_WIDE))
    if match := _CANCEL.search(text):
        return validate_action({"type": "cancel", "match": _message(match["what"])})
    if (match := _STOP.search(text)) and _message(match["what"]):
        return validate_action({"type": "cancel", "match": _message(match["what"])})
    if match := _PAUSE.search(text):
        return validate_action({"type": "pause", "match": _message(match["what"])})
    if match := _RESUME.search(text):
        return validate_action({"type": "resume", "match": _message(match["what"])})
    if match := _DAILY.search(text):
        hour = chinese_number(match["hour"])
        minute = 30 if match["minute"] == "半" else chinese_number(match["minute"]) or 0
        if hour is None:
            return None
        if match["period"] in ("下午", "傍晚", "晚上") and hour < 12:
            hour += 12
        elif match["period"] == "中午" and hour < 11:
            hour += 12
        rest = match["rest"]
        summary = bool(re.search(r"摘要|環境|狀況|報告|空氣", rest)) and "提醒我" not in rest
        message = _message(re.sub(r"^(?:提醒我|叫我)", "", rest))
        return validate_action(
            {
                "type": "schedule",
                "kind": "daily",
                "purpose": "summary" if summary else "remind",
                "message": message or ("環境摘要" if summary else ""),
                "time": f"{hour}:{minute:02d}",
            }
        )
    if match := _INTERVAL.search(text):
        return validate_action(
            {
                "type": "schedule",
                "kind": "interval",
                "message": _message(match["msg"]),
                "minutes": _minutes(match["n"], match["unit"]),
            }
        )
    if match := _ONCE.search(text) or _ONCE_LATER.search(text):
        return validate_action(
            {
                "type": "schedule",
                "kind": "once",
                "message": _message(match["msg"]),
                "minutes": _minutes(match["n"], match["unit"]),
            }
        )
    if match := _SNOOZE_FOR.search(text):
        return validate_action({"type": "snooze", "minutes": _minutes(match["n"], match["unit"])})
    if (match := _FOLLOWUP.search(text)) and re.search(r"了|已經|剛剛|剛", text):
        later = re.search(rf"(?P<n>{NUM}){UNIT}(?:以?後|之後)", text)
        return validate_action(
            {
                "type": "schedule",
                "kind": "followup",
                "message": "我" + match["act"],
                "minutes": _minutes(later["n"], later["unit"]) if later else FOLLOWUP_DEFAULT,
            }
        )
    # Explicit look-ups come before the loose "知道了" snooze phrasing.
    if action := parse_search(spaced):
        return action
    if _SNOOZE.search(text):
        return {"type": "snooze", "minutes": SNOOZE_DEFAULT}
    return None


# ---- Timing ------------------------------------------------------------------------------


def next_daily(hhmm: str, after: datetime, tz: str) -> datetime:
    local = after.astimezone(ZoneInfo(tz))
    hour, minute = (int(part) for part in hhmm.split(":"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(timezone.utc)


def first_run(action: dict, now: datetime, tz: str) -> datetime:
    if action["kind"] == "daily":
        return next_daily(action["time"], now, tz)
    return now + timedelta(minutes=action["minutes"])


def following_run(
    kind: str, minutes: int | None, hhmm: str | None, due: datetime, now: datetime, tz: str
) -> datetime | None:
    """Next due time after a run; None for one-shot jobs. Missed runs are skipped, not replayed."""
    if kind == "interval":
        step = timedelta(minutes=minutes)
        due += step
        while due <= now:
            due += step
        return due
    if kind == "daily":
        return next_daily(hhmm, now, tz)
    return None


def describe(kind, purpose, message, minutes, hhmm, next_run, tz) -> str:
    at = next_run.astimezone(ZoneInfo(tz)).strftime("%H:%M") if next_run else None
    if kind == "once":
        return f"{minutes} 分鐘後（{at}）提醒：{message}"
    if kind == "interval":
        every = f"{minutes // 60} 小時" if minutes % 60 == 0 else f"{minutes} 分鐘"
        return f"每 {every}提醒：{message}" + (f"（下次 {at}）" if at else "")
    if kind == "daily":
        return f"每天 {hhmm} 報環境摘要" if purpose == "summary" else f"每天 {hhmm} 提醒：{message}"
    return f"{minutes} 分鐘後（{at}）回報「{message}」之後的變化"
