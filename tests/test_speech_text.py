"""The speech service's text clean-up, loaded by path (the service itself needs its models)."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("speech_text", Path("speech/text.py"))
speech_text = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speech_text)


@pytest.mark.parametrize(
    "text,spoken",
    [
        ("大約 1,200 ppm", "大约 1200 PPM"),
        ("30–40% 左右", "百分之30到40 左右"),
        ("明天 25-27 度", "明天 25到27 度"),
        ("2026/10/23 下午", "2026年10月23日 下午"),
        ("10/23 有颱風", "10月23日 有台风"),
        ("1/2 杯水", "2分之1 杯水"),
        ("溫度↑ 濕度↓", "温度上升 湿度下降"),
        ("根據cwa.gov.tw，明天下雨", "根据，明天下雨"),
        ("看 https://www.cwa.gov.tw/V8/C 的預報", "看  的预报"),
        ("#1 新聞", "第1 新闻"),
        ("讓我想想…嗯……好", "让我想想，嗯，好"),
        ("明天會下雨（降雨機率 90%）。", "明天会下雨，降雨机率 百分之90。"),
        ("好喔～", "好喔"),
        ("CO₂ 740 ppm，25.4°C", "二氧化碳 740 PPM，25.4度"),
        ("欸，你回來啦！誒？", "诶，你回来啦！诶？"),
        ("Python 3.14 新增 t-string", "Python 3.14 新增 tstring"),
    ],
)
def test_symbols_are_spoken_as_words(text, spoken):
    assert speech_text.speakable(text) == spoken


@pytest.mark.parametrize("text", ["～", "……", "😊", "？！", "（）"])
def test_symbol_only_text_has_nothing_to_say(text):
    assert not speech_text.has_speech(text)
