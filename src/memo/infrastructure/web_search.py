"""Web search through a self-hosted SearXNG instance (JSON API).

Only the query leaves the backend: no readings, conversation or device data. Results are
trimmed to a few short snippets, because MEMO answers in one or two spoken sentences.
"""

import html
import json
import logging
import re
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
CACHE_LIMIT = 100


def plain(text, limit: int) -> str:
    """Text without markup or runs of whitespace, cut to ``limit`` characters."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def answer_text(item) -> str:
    if isinstance(item, dict):
        item = item.get("answer") or item.get("content") or ""
    return plain(item, 160)


class SearxngSearch:
    def __init__(self, settings, opener=urlopen, clock=time.monotonic):
        self.settings = settings
        self.opener = opener
        self.clock = clock
        self.cache: dict[str, tuple[float, dict]] = {}

    def search(self, query: str) -> dict:
        """{"query", "status": ok|empty|unavailable|not_configured, "results", "answers"}."""
        outcome = {"query": query, "status": "not_configured", "results": [], "answers": []}
        if not self.settings.url:
            return outcome
        cached = self.cache.get(query)
        if cached and cached[0] > self.clock():
            return cached[1]
        params = urlencode(
            {"q": query, "format": "json", "language": self.settings.language, "safesearch": 1}
        )
        request = Request(
            f"{self.settings.url.rstrip('/')}/search?{params}",
            headers={"Accept": "application/json", "User-Agent": "MEMO"},
        )
        try:
            with self.opener(request, timeout=self.settings.timeout_seconds) as response:
                data = json.load(response)
        except (OSError, ValueError) as exc:  # network errors, timeouts, HTTP errors, bad JSON
            # Never log the query: it is the user's words.
            logger.warning("Web search unavailable: %s", type(exc).__name__)
            return outcome | {"status": "unavailable"}
        results = []
        for item in data.get("results") or []:
            url = item.get("url") or ""
            host = urlparse(url).hostname
            title = plain(item.get("title"), 80)
            if urlparse(url).scheme not in ("http", "https") or not host or not title:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "source": host.removeprefix("www."),
                    "snippet": plain(item.get("content"), 200),
                }
            )
            if len(results) >= self.settings.max_results:
                break
        answers = [text for text in map(answer_text, data.get("answers") or []) if text][:2]
        outcome |= {
            "status": "ok" if results or answers else "empty",
            "results": results,
            "answers": answers,
        }
        if outcome["status"] == "ok":
            if len(self.cache) >= CACHE_LIMIT:
                self.cache.clear()
            self.cache[query] = (self.clock() + self.settings.cache_seconds, outcome)
        return outcome
