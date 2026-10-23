"""Web search requests: recognising "查一下…" phrasings and validating a model's search action.

Questions about this room stay with the sensors; only explicit look-ups go to the web.
"""

import re

QUERY_MAX = 100
_VERB = re.compile(
    r"(?:上網|網路上?)(?:查|搜尋?|找)(?:一下|看看)?"
    r"|(?:幫我|請)?(?:查|搜|google|谷歌)(?:一下|一查|查看|看看)"
    r"|搜尋(?:一下)?",
    re.IGNORECASE,
)
_ROOM = re.compile(r"這裡|這邊|房間|室內|屋內|感測|提醒|排程")
_METRIC_ONLY = re.compile(
    r"^(?:現在|目前)?的?(?:溫度|濕度|溫濕度|二氧化碳|CO2|CO₂|數值|讀值|空氣)"
    r"(?:多少|如何|怎樣|怎麼樣|狀況)?$",
    re.IGNORECASE,
)


def clean_query(text: str) -> str:
    text = re.sub(r"^[，,：:、「『\s]+|[。！？!?，,」』～~\s]+$", "", text)
    return re.sub(r"[吧呢啦喔哦嗎]+$", "", text).strip()


def validate_search(raw: dict) -> dict | None:
    query = raw.get("query")
    if not isinstance(query, str):
        return None
    query = clean_query(query)[:QUERY_MAX]
    return {"type": "search", "query": query} if query else None


def parse_search(text: str) -> dict | None:
    """An explicit look-up ("查一下明天台北天氣"); None for questions about this room."""
    match = _VERB.search(text)
    if not match:
        return None
    query = clean_query(text[match.end() :])
    if len(query) < 2 or _ROOM.search(query) or _METRIC_ONLY.match(query):
        return None
    return {"type": "search", "query": query[:QUERY_MAX]}
