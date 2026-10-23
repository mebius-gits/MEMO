"""Text normalisation before Kokoro: say symbols aloud the way a person would.

Kokoro's Chinese front end skips or misreads many symbols (1,200 became 一二百, 1/2 became
十2, 2026/10/23 one long number, arrows vanished, web addresses were spelled out letter by
letter). Rewriting them as words keeps speech fluent. Kokoro's lexicon only knows
Simplified characters, so the result is converted last.
"""

import re

from opencc import OpenCC

# Character by character (tw2s), not phrase by phrase (tw2sp): MEMO must say the words
# shown on screen, not mainland substitutes such as 新增 → 添加.
to_simplified = OpenCC("tw2s")

NUMBER = r"\d+(?:\.\d+)?"
DASH = r"\s*[-–—~～]\s*"
WEB = re.compile(
    r"https?://\S+|www\.\S+"
    # Bare domains (cwa.gov.tw), ASCII only so neighbouring Chinese is never swallowed.
    r"|(?<![A-Za-z0-9.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
    r"\.(?:com|net|org|gov|edu|io|tw|cn|jp|hk)(?:\.[A-Za-z]{2})?(?:/[!-~]*)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def fraction_or_date(match: re.Match) -> str:
    """a/b is a fraction when it reads like one (1/2, 3/4), otherwise month/day."""
    top, bottom = int(match[1]), int(match[2])
    if top < bottom <= 10:
        return f"{bottom}分之{top}"
    if 1 <= top <= 12 and 1 <= bottom <= 31:
        return f"{top}月{bottom}日"
    return f"{top}除以{bottom}"


def speakable(text: str) -> str:
    """Spell out units and symbols the Chinese front end would otherwise skip or misread."""
    text = WEB.sub("", text)
    text = re.sub(r"CO\s*[₂2]", "二氧化碳", text, flags=re.IGNORECASE)
    # Dates before ranges and fractions: 2026/10/23, 2026-10-23.
    text = re.sub(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", r"\1年\2月\3日", text)
    # Thousands separators: 1,200 is one number.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    # Ranges: 30–40% is 百分之三十到四十; 25-27 度 is 二十五到二十七度.
    text = re.sub(rf"({NUMBER}){DASH}({NUMBER})\s*[%％]", r"百分之\1到\2", text)
    text = re.sub(rf"({NUMBER})\s*[%％]{DASH}({NUMBER})\s*[%％]", r"百分之\1到\2", text)
    text = re.sub(rf"(?<![\d.])({NUMBER}){DASH}({NUMBER})(?![\d.])", r"\1到\2", text)
    text = re.sub(r"(?<![\d.])(\d{1,3})\s*/\s*(\d{1,3})(?![\d.])", fraction_or_date, text)
    text = re.sub(rf"({NUMBER})\s*°\s*C", r"\1度", text)
    text = re.sub(rf"({NUMBER})\s*[%％]", r"百分之\1", text)
    text = re.sub(r"\s*ppm\b", " PPM", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*lux\b", "勒克斯", text, flags=re.IGNORECASE)
    text = re.sub(r"#\s*(\d+)", r"第\1", text)
    for arrow, word in (("↑", "上升"), ("↓", "下降"), ("→", "到"), ("←", "從")):
        text = text.replace(arrow, word)
    # Pauses: ellipses, dashes and brackets become commas; a lone tilde is just drawn out.
    text = re.sub(r"…+|\.{3,}|——+|—", "，", text)
    text = re.sub(r"[（(]\s*", "，", text)
    text = re.sub(r"\s*[）)]", "，", text)
    text = re.sub(r"[~～]+", "", text)
    # Emoji and other symbols carry no speech.
    text = re.sub(r"[^\w\s，。！？、；：,.!?;:\"'「」『』]", "", text)
    text = re.sub(r"\s*，(?:\s*[，、；：,])+", "，", text)
    text = re.sub(r"^[\s，、；：,]+|[\s，、；：,]+(?=[。！？!?]|$)", "", text)
    # MEMO's signature 欸 is read as ēi only in its Simplified spelling.
    return to_simplified.convert(text).replace("欸", "诶").replace("誒", "诶").strip()


def has_speech(text: str) -> bool:
    """Whether anything is left to say after normalisation."""
    return bool(re.search(r"\w", speakable(text)))
