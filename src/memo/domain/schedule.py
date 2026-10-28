"""Reminders, recurring tasks, follow-ups and snoozes: parsing, validation and timing.

Pure functions. Actions come from two places and share one schema, so both are validated
the same way: the deterministic Chinese phrase parser below, and a model's optional "action".

    {"type": "schedule", "kind": "once|interval|daily|followup", "purpose": "remind|summary",
     "message": "喝水", "minutes": 10, "time": "09:00"}
    {"type": "snooze", "minutes": 30}
    {"type": "cancel" | "pause" | "resume", "match": "喝水"}   # empty match = all
    {"type": "search", "query": "明天台北天氣"}
    {"type": "schedule", "kind": "interval", "purpose": "look", "message": "我有沒有駝背",
     "minutes": 30, "look": {"mode": "check", "question": "使用者是不是駝背？",
     "alert_on": "yes", "notify": "on_alert"}}   # see memo.domain.vision
    {"type": "schedule", "kind": "interval", "purpose": "look", "message": "箱子",
     "minutes": 2, "look": {"mode": "change", "question": "箱子", "watch": true}}
    {"type": "schedule", "kind": "event", "purpose": "look", "message": "窗戶有沒有開",
     "look": {"mode": "check", "question": "窗戶有沒有開？", "on": ["co2_high"]}}
    {"type": "look"}   # look at what the user shows the camera now

An ``event`` job has no time of its own: it is a look task run only by the sensor triggers
in look.on.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from memo.domain.vision import TRIGGER_LABELS, validate_look
from memo.domain.web import parse_search, validate_search

KINDS = ("once", "interval", "daily", "followup", "event")
MINUTE_LIMITS = {"once": (1, 1440), "interval": (5, 1440), "followup": (1, 120)}
# A watched task is watched continuously by the page; its own photos are a safety check
# for what the page cannot tell, so they may come as often as every minute.
WATCH_LIMITS = (1, 1440)
WATCH_MINUTES = 2
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
        if purpose not in ("remind", "summary", "followup", "look"):
            purpose = "remind"
        look = None
        if purpose == "look":
            look = validate_look(raw.get("look"), message)
            if look is None:
                return None
            message = message or look["question"][:MESSAGE_MAX]
        if purpose in ("remind", "followup") and not message:
            return None
        action = {"type": "schedule", "kind": kind, "purpose": purpose, "message": message}
        if look:
            action["look"] = look
        if kind == "event":
            # Only a look task can wait for sensors, and it needs at least one to wait for.
            return action if look and look.get("on") else None
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
            watched = kind == "interval" and look is not None and look["watch"]
            low, high = WATCH_LIMITS if watched else MINUTE_LIMITS[kind]
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
    if kind_of == "look":
        return {"type": "look"}
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
# 「每 30 分鐘幫我看一下我有沒有駝背」: a photo task. Only the model sets those up, since only
# it can tell which answer is worth telling (「電腦有沒有關」 alerts on no), so the parser
# steps aside instead of mistaking the phrase for a plain reminder.
_LOOK_TASK = re.compile(
    r"(?:拍照|拍一?張|看一下|看一眼|看看|幫我看|檢查|確認|盯著?).*"
    r"(?:有沒有|是不是|還在|在不在|了沒|開著|關著)"
)
# 「幫我顧著箱子」「有沒有人動我的東西」「幫我看箱子有沒有被動過」: watching a thing. Set up
# here, not by the model, so it is always a watched change task: one photo answering a yes/no
# question cannot tell whether something was moved, and the user need not say how often.
_WATCH_VERB = re.compile(
    r"(?:幫我|替我|請你?|你)(?:顧|盯|守|看好|看住|看緊|看著)(?:著|住|好)?(?:一下)?"
)
_TOUCHED = re.compile(
    r"有沒有(?:被)?人?(?:動|碰|拿|翻|移)(?:過|走)?|有人(?:動|碰|拿|翻)過?(?=.*(?:就|的話|時))"
)
_YES_NO = re.compile(r"有沒有|是不是|在不在|還在")
_UNWATCH = re.compile(
    r"(?:不用|不要|別|停止|取消)再?(?:幫我)?(?:顧|盯|守|看著)(?:著|住)?(?P<what>.+)$"
)
_EVERY = re.compile(rf"每(?:隔)?(?P<n>{NUM}|半)?{UNIT}")
_LEAD = re.compile(
    r"^(?:可以|能不能|麻煩|請)?你?(?:幫我|替我)?(?:看|檢查|注意|確認|留意)?(?:一下|看)?"
)
_CUT = re.compile(r"[，,。！？!?；;]|有沒有|有人|如果|要是|的話|不要|別讓|以免|被")
_TAIL = re.compile(r"(?:一下|吧|喔|哦|啦|好嗎|好不好|嗎|了)+$")


def _thing(text: str) -> str:
    return _TAIL.sub("", _CUT.split(text, maxsplit=1)[0]).strip()


def parse_watch(text: str) -> dict | None:
    """A watch request in a message without spaces, as a validated action; None otherwise."""
    every = _EVERY.search(text)
    text = _EVERY.sub("", text)
    if verb := _WATCH_VERB.search(text):
        rest = text[verb.end() :]
        if _YES_NO.search(rest) and not _TOUCHED.search(rest):
            return None  # 「盯著我有沒有駝背」 is a yes/no question: the model sets it up
        what = _thing(rest)
    elif touched := _TOUCHED.search(text):
        what = _thing(_LEAD.sub("", text[: touched.start()])) or _thing(text[touched.end() :])
    else:
        return None
    what = what or "這裡的東西"
    minutes = _minutes(every["n"], every["unit"]) if every else WATCH_MINUTES
    return validate_action(
        {
            "type": "schedule",
            "kind": "interval",
            "purpose": "look",
            "message": what,
            "minutes": minutes,
            "look": {"mode": "change", "question": what, "watch": True},
        }
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
    if match := _UNWATCH.search(text):
        what = _thing(match["what"])
        return validate_action({"type": "cancel", "match": what}) if what else None
    if action := parse_watch(text):
        return action
    if re.search(r"每|分鐘|小時|點", text) and _LOOK_TASK.search(text) and "提醒" not in text:
        return None
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


def first_run(action: dict, now: datetime, tz: str) -> datetime | None:
    if action["kind"] == "event":
        return None  # sensors start it
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


def describe(kind, purpose, message, minutes, hhmm, next_run, tz, look=None) -> str:
    at = next_run.astimezone(ZoneInfo(tz)).strftime("%H:%M") if next_run else None
    if purpose == "look":
        triggers = "、".join(TRIGGER_LABELS[item] for item in (look or {}).get("on", ()))
        if kind == "event":
            return f"{triggers}時拍照看看：{message}"
        also = f"；{triggers}時也會看" if triggers else ""
        if kind == "once":
            return f"{minutes} 分鐘後（{at}）拍照看看：{message}{also}"
        if kind == "daily":
            return f"每天 {hhmm} 拍照看看：{message}{also}"
        every = f"{minutes // 60} 小時" if minutes % 60 == 0 else f"{minutes} 分鐘"
        if (look or {}).get("watch"):
            return f"持續盯著：{message}（每 {every}再拍照確認一次{also}）"
        return f"每 {every}拍照看看：{message}" + (f"（下次 {at}）" if at else "") + also
    if kind == "once":
        return f"{minutes} 分鐘後（{at}）提醒：{message}"
    if kind == "interval":
        every = f"{minutes // 60} 小時" if minutes % 60 == 0 else f"{minutes} 分鐘"
        return f"每 {every}提醒：{message}" + (f"（下次 {at}）" if at else "")
    if kind == "daily":
        return f"每天 {hhmm} 報環境摘要" if purpose == "summary" else f"每天 {hhmm} 提醒：{message}"
    return f"{minutes} 分鐘後（{at}）回報「{message}」之後的變化"
