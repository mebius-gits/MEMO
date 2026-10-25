"""What MEMO sees through the camera: look tasks, image quality, scene records, comparison.

Pure functions. A look task is a scheduled job with purpose "look": at each run the web page
takes a photo, a vision model answers the task's question and describes the scene, and
``judge`` compares the answer with what MEMO saw before to decide whether to speak.

Three kinds of task: ``check`` asks a yes/no question ("使用者是不是駝背？") and compares the
answers; ``change`` compares the photo with a reference photo (kept only in the browser) or,
without one, with a text description of it; ``report`` describes the scene every run (定時
告訴我這邊的情況), and MEMO tells it together with the room's readings. A change task with
``watch`` is also watched continuously by the page, which asks for a photo the moment the
watched region changes and stays changed.

Photos are never stored. Every capture, even an unusable or skipped one, is recorded with
its quality and a scene record: people (人), activity (事), time (時, the capture time),
place (地), objects (物) and actions (動作).
"""

import re
from dataclasses import dataclass

from memo.domain.memory import SENSITIVE, clean_text

MODES = ("check", "change", "report")
NOTIFY = ("on_alert", "on_change", "always")
ANSWERS = ("yes", "no", "unknown")
QUESTION_MAX = 60
# The second photo of a confirmation is taken this long after the first.
CONFIRM_GAP_SECONDS = 6
# How long the page has to take a photo once a run is due, and to send the confirming one.
CAPTURE_WINDOW_SECONDS = 120
CONFIRM_WINDOW_SECONDS = 60
# A persisting alert is repeated every REPEAT_EVERY runs, one level more direct each time.
REPEAT_EVERY = 3
MAX_LEVEL = 3
# After this many photos in a row that answer nothing, MEMO says once that it cannot see.
UNSEEN_AFTER = 3

# Image quality, measured in the browser on a grey copy of the photo: mean brightness and
# contrast (standard deviation) on 0-255, and sharpness as the variance of the Laplacian.
QUALITY_KEYS = ("brightness", "contrast", "sharpness", "width", "height")
QUALITY_LIMITS = {
    "dark": ("brightness", "<", 45),
    "overexposed": ("brightness", ">", 220),
    "low_contrast": ("contrast", "<", 15),
    "blurry": ("sharpness", "<", 40),
}
QUALITY_WORDS = {
    "dark": "太暗",
    "overexposed": "過亮",
    "low_contrast": "對比太低",
    "blurry": "模糊",
}
VISIBILITY = ("clear", "partial", "poor")
SCENE_LIST_MAX = {"animals": 5, "objects": 8, "actions": 5}
SCENE_ITEM_MAX = {"animals": 30, "objects": 20, "actions": 30}
SCENE_TEXT_MAX = {"activity": 60, "place": 30, "lighting": 20}


def validate_roi(raw) -> dict | None:
    """A region of the frame as fractions of its size, or None for the whole frame."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y, w, h = (float(raw[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0.05 <= w <= 1 and 0.05 <= h <= 1):
        return None
    if x + w > 1.0001 or y + h > 1.0001:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def validate_look(raw, fallback_question: str = "") -> dict | None:
    """Normalise a look task's settings; None when unusable.

    ``alert_on`` is the answer worth telling: "yes" for 「我有沒有駝背」, "no" for
    「電腦有沒有關機」. ``notify`` decides when MEMO speaks: on_alert (that answer appears,
    persists or goes away), on_change (the answer changes) or always (every run); a report
    always speaks. ``watch`` (change tasks only) keeps the page watching between runs."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode", "check")
    if mode not in MODES:
        return None
    question = clean_text(raw.get("question") or fallback_question, QUESTION_MAX)
    if not question or SENSITIVE.search(question):
        return None
    alert_on = raw.get("alert_on", "yes")
    if alert_on not in ("yes", "no"):
        return None
    notify = "always" if mode == "report" else raw.get("notify", "on_alert")
    if notify not in NOTIFY:
        return None
    look = {"mode": mode, "question": question, "alert_on": alert_on, "notify": notify}
    look["roi"] = validate_roi(raw.get("roi"))
    look["watch"] = mode == "change" and raw.get("watch") is True
    return look


def quality_flags(metrics: dict | None) -> list[str]:
    """Problems with a photo, from the numbers the browser measured. Recorded, not retaken."""
    flags = []
    for flag, (key, op, limit) in QUALITY_LIMITS.items():
        value = (metrics or {}).get(key)
        if isinstance(value, (int, float)) and (value < limit if op == "<" else value > limit):
            flags.append(flag)
    return flags


def clean_quality(raw) -> dict:
    """The measured numbers, bounded; anything missing or odd is left out."""
    metrics = {}
    for key in QUALITY_KEYS:
        value = (raw or {}).get(key) if isinstance(raw, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            metrics[key] = round(float(value), 2)
    return metrics


def _item(value, limit):
    text = clean_text(value, limit)
    return text if text and not SENSITIVE.search(text) else ""


def clean_scene(raw) -> dict:
    """A bounded scene record from model output: 人、事、地、物、動作, animals and lighting.

    The time (時) is the capture time, kept by the backend, never taken from the model."""
    raw = raw if isinstance(raw, dict) else {}
    people = raw.get("people") if isinstance(raw.get("people"), dict) else {}
    count = people.get("count")
    scene = {
        "people": {
            "count": count
            if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 50
            else None,
            "description": _item(people.get("description"), 60),
        }
    }
    for key, limit in SCENE_TEXT_MAX.items():
        scene[key] = _item(raw.get(key), limit)
    for key, size in SCENE_LIST_MAX.items():
        items = raw.get(key) if isinstance(raw.get(key), list) else []
        kept = []
        for item in items:
            text = _item(item, SCENE_ITEM_MAX[key])
            if text and text not in kept:
                kept.append(text)
        scene[key] = kept[:size]
    return scene


def scene_text(scene: dict | None, limit: int = 160) -> str:
    """One line summarising a scene record, for baselines and for MEMO's prompts."""
    if not scene:
        return ""
    parts = []
    if scene.get("place"):
        parts.append(f"地點：{scene['place']}")
    people = scene.get("people") or {}
    if people.get("count") is not None or people.get("description"):
        who = f"{people['count']} 人" if people.get("count") is not None else ""
        if people.get("description"):
            who = f"{who}，{people['description']}" if who else people["description"]
        parts.append(f"人：{who}")
    if scene.get("animals"):
        parts.append("動物：" + "、".join(scene["animals"]))
    if scene.get("activity"):
        parts.append(f"事：{scene['activity']}")
    if scene.get("objects"):
        parts.append("物：" + "、".join(scene["objects"]))
    if scene.get("actions"):
        parts.append("動作：" + "、".join(scene["actions"]))
    if scene.get("lighting"):
        parts.append(f"光線：{scene['lighting']}")
    return clean_text("；".join(parts), limit)


def parse_observation(raw, mode: str) -> dict:
    """The model's answer for a photo. Raises ValueError when the answer itself is unusable;
    a broken scene only loses detail, since the answer is what the comparison needs. A
    report's answer is its summary ("seen"), or "unknown" when the model could not tell."""
    if not isinstance(raw, dict):
        raise ValueError("observation is not an object")
    visibility = raw.get("visibility")
    if mode == "report":
        summary = clean_text(raw.get("summary"), 80)
        if not summary and raw.get("answer") != "unknown":
            raise ValueError("report without a summary")
        return {
            "result": "seen" if summary else "unknown",
            "detail": summary or None,
            "visibility": visibility if visibility in VISIBILITY else None,
            "scene": clean_scene(raw.get("scene")),
        }
    answer = raw.get("changed" if mode == "change" else "answer")
    answer = answer.strip().lower() if isinstance(answer, str) else answer
    if answer not in ANSWERS:
        raise ValueError("observation answer is not yes, no or unknown")
    return {
        "result": answer,
        "detail": clean_text(raw.get("detail"), 80) or None,
        "visibility": visibility if visibility in VISIBILITY else None,
        "scene": clean_scene(raw.get("scene")),
    }


@dataclass(frozen=True)
class Verdict:
    """What to do with an observation.

    decision: done (nothing to say), confirm (take the second photo first), notify, unconfirmed
    (the second photo disagreed; treated as a misread), baseline (a change task's reference).
    notice: alert, repeat, recovered, changed, report or unseen; level 1-3 for alerts."""

    decision: str
    state: dict
    notice: str | None = None
    level: int | None = None
    store_reference: bool = False


def alerting(look: dict, result: str) -> bool:
    if look["mode"] == "change":
        return result == "yes"
    return result == look["alert_on"]


def repeat_level(streak: int) -> int | None:
    """Level of the notice for the ``streak``-th alerting run in a row, or None to stay quiet."""
    if streak == 1:
        return 1
    if streak > 1 and (streak - 1) % REPEAT_EVERY == 0:
        return min(MAX_LEVEL, 1 + (streak - 1) // REPEAT_EVERY)
    return None


def _settled(look: dict, state: dict, result: str, at: str, scene_line: str) -> Verdict:
    """The verdict once a result is trusted (confirmed, or needing no confirmation)."""
    state = dict(state)
    previous = state.get("last")
    notify = look["notify"]
    state["unknown_streak"] = 0
    state["last_at"] = at
    if look["mode"] == "report":
        state.update(last="seen", last_summary=scene_line)
        return Verdict("notify", state, "report")
    if look["mode"] == "change":
        if result == "yes":
            # The scene changed: tell once, then the current scene becomes the reference.
            state.update(last="no", changed_at=at, baseline_text=scene_line, baseline_at=at)
            state["changes"] = state.get("changes", 0) + 1
            return Verdict("notify", state, "changed", 1, store_reference=True)
        state["last"] = "no"
        if notify == "always":
            return Verdict("notify", state, "report")
        return Verdict("done", state)
    alert = alerting(look, result)
    was_alerting = state.get("alert_streak", 0) > 0
    state["alert_streak"] = state.get("alert_streak", 0) + 1 if alert else 0
    if result != previous:
        state["changed_at"] = at
    state["last"] = result
    if notify == "always":
        return Verdict("notify", state, "report", repeat_level(1) if alert else None)
    if notify == "on_change":
        if previous is None or result != previous:
            return Verdict("notify", state, "report")
        return Verdict("done", state)
    if alert:
        level = repeat_level(state["alert_streak"])
        if level is None:
            return Verdict("done", state)
        return Verdict("notify", state, "alert" if level == 1 else "repeat", level)
    if was_alerting:
        return Verdict("notify", state, "recovered")
    return Verdict("done", state)


def needs_confirmation(look: dict, state: dict, verdict: Verdict) -> bool:
    """A notice about a new situation waits for a second photo; steady repeats do not."""
    if verdict.decision != "notify" or look["notify"] == "always":
        return False
    if verdict.notice in ("alert", "recovered", "changed"):
        return True
    # on_change: the very first answer is reported straight away.
    return verdict.notice == "report" and state.get("last") is not None


def judge(
    look: dict,
    state: dict | None,
    result: str,
    at: str,
    *,
    attempt: int = 1,
    first: str | None = None,
    scene_line: str = "",
    baseline: bool = False,
    confirmed: bool = False,
) -> Verdict:
    """Compare an observation with what MEMO knew; returns the decision and the new state.

    ``state`` holds last (the trusted answer), alert_streak, unknown_streak and, for change
    tasks, baseline_text. ``attempt`` 2 is the confirming photo and ``first`` the answer the
    first photo gave. ``baseline`` marks a change task's first photo, which only describes.
    ``confirmed``: the page already saw the change hold for several seconds (watching), so
    no second photo is needed."""
    state = dict(state or {})
    if baseline:
        state.update(
            last="no", last_at=at, baseline_text=scene_line, baseline_at=at, unknown_streak=0
        )
        return Verdict("baseline", state, store_reference=True)
    if result == "unknown":
        if attempt == 2:
            return Verdict("unconfirmed", state)
        state["unknown_streak"] = state.get("unknown_streak", 0) + 1
        if state["unknown_streak"] == UNSEEN_AFTER:
            return Verdict("notify", state, "unseen")
        return Verdict("done", state)
    if attempt == 2 and result != first:
        return Verdict("unconfirmed", state)
    verdict = _settled(look, state, result, at, scene_line)
    if attempt == 1 and not confirmed and needs_confirmation(look, state, verdict):
        return Verdict("confirm", state)
    return verdict


# ---- Asking to look ----------------------------------------------------------------------

# Chat phrasings that mean "look at this through the camera": the page takes a photo before
# sending. Kept narrow on purpose; the model's {"type":"look"} action covers the rest.
LOOK_REQUEST = re.compile(
    r"手上|手裡|手中|拿著|拿的(是|東西)|這(個|是|張|本|瓶|罐|盒|些|上面)?是什麼|這是啥|"
    r"翻譯(一下)?(這|它|上面|看看)|上面寫(的|著)?(是)?什麼|寫(的|著)?是什麼|"
    r"(看|瞧)(一下|看)?(這個|這|我手|我拿|我現在)|你看得到(我|這)|幫我看(一下|看)?(這|我)"
)
# Topics that bring the look tasks' records into a chat reply.
VISION_TOPIC = re.compile(
    r"拍照|拍的|照片|鏡頭|相機|看到|看見|畫面|觀察|紀錄|記錄|駝背|姿勢|坐姿|座位|桌上|"
    r"電腦|螢幕|關機|有沒有人|剛剛看|任務"
)


def asks_to_look(text: str) -> bool:
    return bool(LOOK_REQUEST.search(text or ""))


def talks_about_vision(text: str, questions=()) -> bool:
    """Whether a chat message is about what the look tasks saw: a topic word, or two
    characters in a row shared with a task's question (「駝背」 in 「我有沒有駝背」)."""
    text = text or ""
    if VISION_TOPIC.search(text):
        return True
    for question in questions:
        core = re.sub(r"使用者|我|有沒有|是不是|還在|嗎|？|\?", "", question)
        if any(core[i : i + 2] in text for i in range(len(core) - 1)):
            return True
    return False
