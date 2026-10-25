"""MEMO's own observation of the space: the scene now, what changed, what is worth saying.

Pure functions. While the camera is on, the web page watches the whole frame and asks for an
observation when the scene changes and settles, when someone appears or leaves, and on a
regular patrol. The vision model describes the photo (memo.domain.vision.clean_scene); this
module keeps the situation (who and what is here), turns differences into events for the
observation log, and decides which events MEMO says out loud. As with the room's readings,
rules decide whether to speak; the model only describes the photo and words the notice.

It also tells the two kinds of conversation apart: an assignment (watch this, report, what
happened) is answered from records first; an interaction (everyday chat) is just chat.
"""

import re
from datetime import datetime, timedelta

from memo.domain.vision import talks_about_vision

TRIGGERS = ("patrol", "scene_change", "person_enter", "person_leave")
# The page waits this long between observations, and patrols at this interval.
MIN_GAP_SECONDS = 20
PATROL_MINUTES = 15
# Someone back after this long is greeted (with what happened meanwhile).
AWAY_FOR_GREETING = timedelta(minutes=10)
# The same kind of animal is mentioned at most this often.
ANIMAL_COOLDOWN = timedelta(minutes=30)
# Without an observation this recent, MEMO does not know whether anyone is here.
PRESENCE_FRESH = timedelta(minutes=10)
LOG_DAYS = 30

# What each event is called in the log, and whether it can be spoken.
EVENT_WORDS = {
    "start": "開始觀察",
    "arrival": "有人出現",
    "departure": "沒有人了",
    "animal": "動物出現",
    "animal_gone": "動物離開",
    "lights": "光線變化",
    "watched_change": "看守的東西有變化",
    "task": "拍照任務",
}
_COUNT = re.compile(r"^(?:[一二兩三四五六七八九十幾\d]+|一些|好幾)[隻條頭個位只]?")
_DARK = re.compile(r"暗|昏|黑|關燈|沒開燈")
_BRIGHT = re.compile(r"亮|明|日光|開燈|陽光")


def animal_kind(text: str) -> str:
    """「一隻貓（趴在箱子上）」 → 「貓」: the kind of animal, for comparing scenes."""
    kind = _COUNT.sub("", (text or "").strip())
    return re.split(r"[（(，,、 在正]", kind, maxsplit=1)[0].strip()


def light_level(lighting: str) -> str | None:
    if not lighting:
        return None
    if _DARK.search(lighting):
        return "dark"
    if _BRIGHT.search(lighting):
        return "bright"
    return None


def situation(scene: dict | None, summary: str | None, at: datetime) -> dict:
    """The scene as MEMO keeps it: how many people, which animals, the light, a summary."""
    scene = scene or {}
    people = (scene.get("people") or {}).get("count")
    return {
        "at": at.isoformat(),
        "people": people,
        "people_detail": (scene.get("people") or {}).get("description") or "",
        "animals": list(scene.get("animals") or []),
        "lighting": scene.get("lighting") or "",
        "place": scene.get("place") or "",
        "activity": scene.get("activity") or "",
        "summary": summary or "",
    }


def changes(previous: dict | None, current: dict) -> list[dict]:
    """Events between two situations: people coming and going, animals, the light.

    Objects are left to change tasks: a model's list of things on a desk varies from photo
    to photo, so comparing it would produce noise rather than news."""
    if not previous:
        return [{"kind": "start", "detail": current.get("summary") or ""}]
    events = []
    before, now = previous.get("people"), current.get("people")
    if before is not None and now is not None:
        if before == 0 and now > 0:
            events.append({"kind": "arrival", "detail": current.get("people_detail") or ""})
        elif before > 0 and now == 0:
            events.append({"kind": "departure", "detail": ""})
    old = {animal_kind(item): item for item in previous.get("animals") or []}
    new = {animal_kind(item): item for item in current.get("animals") or []}
    for kind, item in new.items():
        if kind and kind not in old:
            events.append({"kind": "animal", "detail": item, "animal": kind})
    for kind, item in old.items():
        if kind and kind not in new:
            events.append({"kind": "animal_gone", "detail": item, "animal": kind})
    light_before, light_now = (
        light_level(previous.get("lighting")),
        light_level(current.get("lighting")),
    )
    if light_before and light_now and light_before != light_now:
        events.append({"kind": "lights", "detail": current.get("lighting")})
    return events


def worth_saying(event: dict, memory: dict, now: datetime) -> bool:
    """Whether MEMO says this event out loud; everything is logged either way.

    ``memory`` holds empty_since (when the space became empty) and the last time each kind
    of animal was mentioned. A greeting needs a real absence; an animal is mentioned once
    in a while, not every time it walks back into view."""
    if event["kind"] == "arrival":
        since = memory.get("empty_since")
        return bool(since) and now - datetime.fromisoformat(since) >= AWAY_FOR_GREETING
    if event["kind"] == "animal":
        told = (memory.get("animals_told") or {}).get(event.get("animal", ""))
        return not told or now - datetime.fromisoformat(told) >= ANIMAL_COOLDOWN
    return False


def remember(memory: dict, events: list[dict], spoken: list[dict], now: datetime) -> dict:
    """The bookkeeping ``worth_saying`` needs, after a set of events."""
    memory = dict(memory or {})
    for event in events:
        if event["kind"] == "departure":
            memory["empty_since"] = now.isoformat()
        elif event["kind"] == "arrival":
            memory["empty_since"] = None
    told = dict(memory.get("animals_told") or {})
    for event in spoken:
        if event["kind"] == "animal":
            told[event["animal"]] = now.isoformat()
    memory["animals_told"] = told
    return memory


def nobody_here(state: dict | None, now: datetime) -> bool:
    """True only when a recent observation saw no one; unknown counts as maybe someone."""
    if not state or state.get("people") != 0 or not state.get("at"):
        return False
    return now - datetime.fromisoformat(state["at"]) <= PRESENCE_FRESH


# ---- Assignment or interaction ----------------------------------------------------------

# Asking MEMO to watch, report or recall what it observed, or about the readings. Answered from
# records, facts first. Feelings (「我最近情況不太好」「好悶」) stay chat.
ASSIGNMENT = re.compile(
    r"幫我(顧|盯|看著|觀察|注意|記錄|確認|檢查)|(顧|盯)(著|一下)(我的|這個|那個|門|桌)|定時|環境|每(隔)?\s*[0-9零一二兩三四五六七八九十半]+\s*"
    r"(分鐘|分|小時|個小時)|回報|報告一下|現在(這邊|這裡|房間|家裡)?(怎樣|怎麼樣|如何)|"
    r"這(邊|裡)(怎樣|怎麼樣)|有誰|誰來過|剛剛(發生|有什麼|怎麼了)|今天(發生|有什麼)|觀察|日誌|"
    r"溫度|濕度|溼度|二氧化碳|CO\s*[2₂]|空氣|幾度|多少度|數值|數據|光線|亮度"
)
# 「現在這邊怎樣」: the page takes a photo straight away (no countdown) and MEMO reports
# what it sees together with the readings. Same list as the page's wantsReport.
REPORT_NOW = re.compile(
    r"現在(這邊|這裡|房間|家裡)?(怎樣|怎麼樣|如何|的?情況|的?狀況)|這(邊|裡)(現在)?(怎樣|怎麼樣|的?情況|的?狀況)|"
    r"看一下(現在|四周|周圍|房間|這邊)|幫我看看(四周|周圍|房間|這邊)"
)


def conversation_mode(text: str, questions=()) -> str:
    """ "assignment" when the person asks MEMO to watch, report or recall; else "interaction"."""
    text = text or ""
    if ASSIGNMENT.search(text) or REPORT_NOW.search(text) or talks_about_vision(text, questions):
        return "assignment"
    return "interaction"


def asks_for_report(text: str) -> bool:
    return bool(REPORT_NOW.search(text or ""))
