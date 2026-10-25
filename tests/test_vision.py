"""Look tasks, pure parts: settings, photo quality, scene records and the comparison."""

import pytest

from memo.domain.schedule import describe, parse_intent, validate_action
from memo.domain.vision import (
    UNSEEN_AFTER,
    asks_to_look,
    clean_scene,
    judge,
    parse_observation,
    quality_flags,
    scene_text,
    talks_about_vision,
    validate_look,
    validate_roi,
)

CHECK = validate_look({"question": "使用者是不是駝背？"})
OFF = validate_look({"question": "電腦有沒有關機？", "alert_on": "no"})
CHANGE = validate_look({"mode": "change", "question": "桌上的包裹"})


def test_look_settings_are_normalised_and_bounded():
    assert CHECK == {
        "mode": "check",
        "question": "使用者是不是駝背？",
        "alert_on": "yes",
        "notify": "on_alert",
        "roi": None,
        "watch": False,
    }
    assert validate_look({}, "我有沒有駝背")["question"] == "我有沒有駝背"
    assert validate_look({"mode": "watch", "question": "x"}) is None
    assert validate_look({"question": "x", "alert_on": "maybe"}) is None
    assert validate_look({"question": "x", "notify": "sometimes"}) is None
    assert validate_look({"question": ""}) is None
    assert validate_look({"question": "我的密碼還在桌上嗎"}) is None
    assert validate_roi({"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5}) == {
        "x": 0.1,
        "y": 0.2,
        "w": 0.5,
        "h": 0.5,
    }
    assert validate_roi({"x": 0.8, "y": 0, "w": 0.5, "h": 0.5}) is None  # leaves the frame
    assert validate_roi({"x": 0, "y": 0, "w": 0.01, "h": 0.5}) is None  # too small to see
    assert validate_roi("all") is None


def test_quality_is_measured_not_retaken():
    assert quality_flags({"brightness": 120, "contrast": 50, "sharpness": 300}) == []
    assert quality_flags({"brightness": 20, "contrast": 8, "sharpness": 5}) == [
        "dark",
        "low_contrast",
        "blurry",
    ]
    assert quality_flags({"brightness": 240}) == ["overexposed"]
    assert quality_flags(None) == [] and quality_flags({"brightness": "dark"}) == []


def test_scene_record_keeps_who_what_where_things_and_actions():
    scene = clean_scene(
        {
            "people": {"count": 1, "description": "坐在書桌前，背有點彎"},
            "activity": "在電腦前工作",
            "place": "書桌前",
            "objects": ["筆電", "杯子", "筆電", "我的密碼本", "x" * 50],
            "actions": ["打字", "低頭"],
            "lighting": "明亮",
            "extra": "ignored",
        }
    )
    assert scene["people"] == {"count": 1, "description": "坐在書桌前，背有點彎"}
    assert scene["objects"][:2] == ["筆電", "杯子"] and len(scene["objects"]) == 3
    assert "我的密碼本" not in scene["objects"] and len(scene["objects"][2]) <= 20
    assert "extra" not in scene
    line = scene_text(scene)
    assert line.startswith(
        "地點：書桌前；人：1 人，坐在書桌前，背有點彎；事：在電腦前工作；物：筆電、杯子"
    )
    assert line.endswith("動作：打字、低頭；光線：明亮") and len(line) <= 160
    broken = clean_scene({"people": "many", "objects": "筆電", "place": 3})
    assert broken["people"] == {"count": None, "description": ""} and broken["objects"] == []
    assert scene_text(None) == ""


def test_observation_answer_must_be_usable():
    parsed = parse_observation(
        {"answer": "YES", "detail": "背彎著", "visibility": "clear", "scene": {}}, "check"
    )
    assert parsed["result"] == "yes" and parsed["detail"] == "背彎著"
    assert parse_observation({"changed": "no"}, "change")["result"] == "no"
    with pytest.raises(ValueError):
        parse_observation({"answer": "maybe"}, "check")
    with pytest.raises(ValueError):
        parse_observation({"answer": "yes"}, "change")  # change tasks answer "changed"
    with pytest.raises(ValueError):
        parse_observation(["yes"], "check")


def play(look, answers, state=None):
    """Run answers through judge the way the capture flow does, confirming when asked."""
    state, log = dict(state or {}), []
    for i, answer in enumerate(answers):
        second = None
        if isinstance(answer, tuple):
            answer, second = answer
        verdict = judge(look, state, answer, f"t{i}")
        if verdict.decision == "confirm":
            verdict = judge(look, state, second or answer, f"t{i}b", attempt=2, first=answer)
        state = verdict.state
        log.append((verdict.decision, verdict.notice, verdict.level))
    return state, log


def test_a_new_alert_is_confirmed_then_repeated_more_directly():
    first = judge(CHECK, {}, "yes", "t0")
    assert first.decision == "confirm" and first.state == {}  # nothing trusted yet
    state, log = play(CHECK, ["yes"] * 10)
    assert log[0] == ("notify", "alert", 1)
    spoken = [(i + 1, level) for i, (decision, _, level) in enumerate(log) if decision == "notify"]
    assert spoken == [(1, 1), (4, 2), (7, 3), (10, 3)]
    assert state["alert_streak"] == 10 and state["last"] == "yes"
    state, log = play(CHECK, ["no", "no"], state)
    assert log == [("notify", "recovered", None), ("done", None, None)]
    assert state["alert_streak"] == 0


def test_a_second_photo_that_disagrees_is_a_misread():
    state, log = play(CHECK, [("yes", "no")])
    assert log == [("unconfirmed", None, None)] and state == {}
    state, log = play(CHECK, [("yes", "unknown")])
    assert log == [("unconfirmed", None, None)]


def test_alert_on_no_for_things_that_should_be_off():
    _, log = play(OFF, ["yes", "no"])
    assert log == [("done", None, None), ("notify", "alert", 1)]


def test_unclear_photos_are_told_once():
    state, log = play(CHECK, ["unknown"] * (UNSEEN_AFTER + 1))
    assert [entry[1] for entry in log] == [None, None, "unseen", None]
    assert "last" not in state
    state, _ = play(CHECK, ["no"], state)
    assert state["unknown_streak"] == 0


def test_on_change_and_always():
    on_change = CHECK | {"notify": "on_change"}
    _, log = play(on_change, ["no", "no", "yes", "yes", "no"])
    assert [entry[:2] for entry in log] == [
        ("notify", "report"),  # the first answer is reported straight away
        ("done", None),
        ("notify", "report"),
        ("done", None),
        ("notify", "report"),
    ]
    assert judge(on_change, {"last": "no"}, "yes", "t").decision == "confirm"
    always = CHECK | {"notify": "always"}
    _, log = play(always, ["no", "yes"])
    assert log == [("notify", "report", None), ("notify", "report", 1)]
    assert judge(always, {}, "yes", "t").decision == "notify"  # no confirmation needed


def test_change_task_moves_its_baseline_after_telling():
    baseline = judge(CHANGE, {}, "no", "t0", baseline=True, scene_line="桌上有紙箱")
    assert baseline.decision == "baseline" and baseline.store_reference
    assert baseline.state["baseline_text"] == "桌上有紙箱"
    state = baseline.state
    assert judge(CHANGE, state, "no", "t1").decision == "done"
    assert judge(CHANGE, state, "yes", "t2").decision == "confirm"
    told = judge(CHANGE, state, "yes", "t2b", attempt=2, first="yes", scene_line="桌上空了")
    assert (told.decision, told.notice, told.store_reference) == ("notify", "changed", True)
    assert told.state["baseline_text"] == "桌上空了" and told.state["last"] == "no"
    assert told.state["changes"] == 1


def test_look_requests_and_vision_topics():
    for text in ["MEMO 我手上拿著什麼", "這是什麼？", "幫我翻譯這個", "上面寫什麼", "你看得到我嗎"]:
        assert asks_to_look(text), text
    for text in ["今天天氣如何", "看看明天行程", "我好累"]:
        assert not asks_to_look(text), text
    assert talks_about_vision("你剛剛拍照看到什麼")
    assert talks_about_vision("我今天駝背幾次", ["使用者是不是駝背？"])
    assert not talks_about_vision("晚餐吃什麼", ["使用者是不是駝背？"])


def test_look_jobs_are_left_to_the_model_and_validated():
    # The parser cannot tell which answer is worth telling, so it steps aside.
    assert parse_intent("每 30 分鐘幫我看一下我有沒有駝背") is None
    assert parse_intent("每天 23 點看一下電腦有沒有關") is None
    # Reminders that mention looking stay reminders.
    assert parse_intent("每 30 分鐘提醒我看一下有沒有坐好")["purpose"] == "remind"
    action = validate_action(
        {
            "type": "schedule",
            "kind": "interval",
            "purpose": "look",
            "minutes": 30,
            "look": {"question": "使用者是不是駝背？"},
        }
    )
    assert action["message"] == "使用者是不是駝背？" and action["look"]["alert_on"] == "yes"
    bad = {"type": "schedule", "kind": "interval", "purpose": "look", "minutes": 30}
    assert validate_action(bad | {"look": {"question": ""}}) is None
    assert validate_action({"type": "look"}) == {"type": "look"}
    assert describe("interval", "look", "駝背", 30, None, None, "Asia/Taipei") == (
        "每 30 分鐘拍照看看：駝背"
    )
    assert describe("daily", "look", "電腦", None, "23:00", None, "Asia/Taipei") == (
        "每天 23:00 拍照看看：電腦"
    )


# ---- The vision model adapter ------------------------------------------------------------

import io  # noqa: E402
import json  # noqa: E402

from memo.infrastructure.settings import Settings  # noqa: E402
from memo.infrastructure.vision_model import VisionAnalyzer, VisionFailure  # noqa: E402


def model(content, captured=None):
    def opener(request, timeout):
        if captured is not None:
            captured.update(payload=json.loads(request.data), timeout=timeout)
        if isinstance(content, Exception):
            raise content
        body = {"choices": [{"message": {"content": content}}]}
        return io.BytesIO(json.dumps(body).encode())

    settings = Settings(
        openai_base_url="http://model.local/v1",
        openai_model="vision-model",
        openai_vision_timeout_seconds=7,
        _env_file=None,
    )
    return VisionAnalyzer(settings, opener)


def test_analyzer_sends_photos_and_reads_the_observation():
    captured = {}
    reply = (
        '前言 {"changed":"yes","detail":"包裹不见了","visibility":"clear","scene":{"place":"桌上"}}'
    )
    analyzer = model(reply, captured)
    observation = analyzer.analyze("room", CHANGE, "data:b", reference="data:a")
    assert observation["result"] == "yes"
    assert observation["detail"] == "包裹不見了"  # Simplified slips are converted
    assert observation["scene"]["place"] == "桌上"
    content = captured["payload"]["messages"][-1]["content"]
    assert [part["image_url"]["url"] for part in content[1:]] == ["data:a", "data:b"]
    assert "第一張是基準照片" in json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["timeout"] == 7
    # Without a reference photo, the baseline's description stands in for it.
    model(reply, captured).analyze("room", CHANGE, "data:b", baseline_text="桌上有紙箱")
    content = captured["payload"]["messages"][-1]["content"]
    assert len(content) == 2  # one photo
    assert "桌上有紙箱" in json.dumps(captured["payload"], ensure_ascii=False)


def test_analyzer_failures_have_reasons():
    with pytest.raises(VisionFailure) as failure:
        model('{"answer":"maybe"}').analyze("room", CHECK, "data:x")
    assert failure.value.reason == "invalid_model_output"
    with pytest.raises(VisionFailure) as failure:
        model(TimeoutError()).analyze("room", CHECK, "data:x")
    assert failure.value.reason == "model_timeout"
    blind = VisionAnalyzer(Settings(openai_model="m", openai_vision=False, _env_file=None))
    assert blind.unavailable == "vision_unavailable"
    assert VisionAnalyzer(Settings(_env_file=None)).unavailable == "model_not_configured"


def test_analyzer_reports_scenes_and_sends_the_moment_last():
    captured = {}
    reply = '{"summary":"一隻貓趴在箱子上","answer":"seen","scene":{"animals":["一隻貓"]}}'
    observation = model(reply, captured).analyze(
        "room", {"mode": "report", "question": "這邊"}, "data:x"
    )
    assert observation["result"] == "seen" and observation["scene"]["animals"] == ["一隻貓"]
    prompt = json.dumps(captured["payload"], ensure_ascii=False)
    assert "summary 用一句話" in prompt and "不要從照片推測溫度" in prompt
    model('{"changed":"yes","detail":"箱子不見了"}', captured).analyze(
        "room", CHANGE, "data:now", reference="data:ref", moment="data:moment"
    )
    content = captured["payload"]["messages"][-1]["content"]
    assert [part["image_url"]["url"] for part in content[1:]] == [
        "data:ref",
        "data:now",
        "data:moment",
    ]
    assert "最後一張是變化剛開始時的畫面" in json.dumps(captured["payload"], ensure_ascii=False)
