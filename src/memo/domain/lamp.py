"""The small desk lamp beside MEMO: off, or one of three light modes.

The person switches it from the web page; MEMO only knows which mode is on and cannot
change it. A request is an absolute mode or a relative step, resolved against the current
state.
"""

# From dark to bright.
MODES = ("off", "dim", "warm", "day")
LABELS = {"off": "關燈", "dim": "暗光", "warm": "溫暖光", "day": "白天光"}
REQUESTS = (*MODES, "on", "brighter", "dimmer")
DEFAULT_MODE = "day"
DEFAULT_ON = "warm"


def resolve(request: str, current: str, last_on: str | None) -> str:
    if request == "on":
        return current if current != "off" else last_on or DEFAULT_ON
    if request in ("brighter", "dimmer"):
        step = 1 if request == "brighter" else -1
        index = min(max(MODES.index(current) + step, 0), len(MODES) - 1)
        return MODES[index]
    return request
