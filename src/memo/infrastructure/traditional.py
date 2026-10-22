"""Turn stray Simplified characters in model output into Traditional, leaving the rest alone.

Converting whole replies with OpenCC would rewrite correct Traditional text (干擾 → 幹擾,
台灣 → 臺灣). Instead:

- Simplified-only characters (no Traditional use of their own, e.g. 记 → 記) are replaced.
- Shared characters (干, 台, 里, 后...) are kept, unless a neighbour is Simplified-only:
  then they belong to a Simplified word and follow it (这里 → 這裡, 干净 → 乾淨).

Replacements come from OpenCC's phrase-aware Taiwan conversion, so context picks the
candidate (头发 → 頭髮).
"""

from functools import lru_cache
from pathlib import Path

import opencc

# Everyday Taiwanese forms where OpenCC prefers a dictionary variant.
PREFERRED = {"溼": "濕"}
# Shared in theory, but Taiwan writes 著; treat as Simplified-only.
FORCED = {"着": "著"}


@lru_cache(maxsize=1)
def _tables():
    table = Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt"
    simplified_only, shared = dict(FORCED), set()
    for line in table.read_text(encoding="utf-8").splitlines():
        source, _, candidates = line.partition("\t")
        options = candidates.split()
        if len(source) != 1 or not options or source in FORCED:
            continue
        if source in options:
            shared.add(source)
        else:
            simplified_only[source] = options[0]
    return simplified_only, shared, opencc.OpenCC("s2tw")


def to_traditional(text: str) -> str:
    simplified_only, shared, phrases = _tables()
    if not any(char in simplified_only for char in text):
        return text
    converted = phrases.convert(text)
    if len(converted) != len(text):
        # Phrase conversion changed the length; fall back to per-character candidates.
        converted = "".join(simplified_only.get(char, char) for char in text)
    out = []
    for i, char in enumerate(text):
        neighbours = text[max(0, i - 1) : i] + text[i + 1 : i + 2]
        if char in simplified_only:
            new = FORCED.get(char, converted[i])
        elif char in shared and any(n in simplified_only for n in neighbours):
            new = converted[i]
        else:
            new = char
        out.append(PREFERRED.get(new, new) if new != char else new)
    return "".join(out)
