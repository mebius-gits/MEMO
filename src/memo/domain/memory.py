"""Conversation memory: episodes, compression plans and the long-term profile. Pure functions.

A conversation pauses for more than EPISODE_GAP and the next message starts a new episode.
Inside an episode MEMO sees the latest turns verbatim plus a running summary of older ones;
a finished episode is compressed into one short summary. The same compression step keeps a
small profile of what the person has said about themselves (name, likes, projects).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

EPISODE_GAP = timedelta(minutes=60)
# Once an open episode has more than COMPRESS_AT uncompressed turns, all but the newest
# KEEP_TURNS are folded into its running summary (in batches, not one model call per turn).
KEEP_TURNS = 10
COMPRESS_AT = 14
EARLIER_EPISODES = 3
SUMMARY_MAX = 160
PROFILE_KEYS = ("preferences", "projects", "other")
PROFILE_ITEMS = 8
PROFILE_ITEM_MAX = 40
# Never kept even if a model proposes it: contact details, credentials and identity numbers.
SENSITIVE = re.compile(
    r"\d[\d\s-]{7,}\d|[\w.+-]+@[\w-]+\.[\w.]+|密碼|帳號|身分證|身份證|信用卡|地址|住址|銀行",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Turn:
    id: int
    at: datetime
    user: str
    memo: str
    image: bool = False


@dataclass(frozen=True)
class Summary:
    kind: str  # running | episode
    first_turn_id: int
    last_turn_id: int
    started_at: datetime
    ended_at: datetime
    text: str


@dataclass(frozen=True)
class Job:
    """Fold `turns` (and `base`, the running summary they continue) into a new summary."""

    kind: str  # running | episode
    turns: tuple[Turn, ...]
    base: Summary | None


def leading_episode(turns: list[Turn], gap: timedelta = EPISODE_GAP) -> list[Turn]:
    """The first turns that belong together: no pause longer than `gap` between them."""
    episode = turns[:1]
    for turn in turns[1:]:
        if turn.at - episode[-1].at > gap:
            break
        episode.append(turn)
    return episode


def trailing_episode(turns: list[Turn], gap: timedelta = EPISODE_GAP) -> list[Turn]:
    """The last turns that belong together."""
    episode = turns[-1:]
    for turn in reversed(turns[:-1]):
        if episode[0].at - turn.at > gap:
            break
        episode.insert(0, turn)
    return episode


def next_job(
    pending: list[Turn], last: Summary | None, now: datetime, gap: timedelta = EPISODE_GAP
) -> Job | None:
    """The oldest compression still due for a session, or None.

    `pending` are the turns after the last summary, oldest first. A finished episode becomes
    one episode summary; an open one only folds its older turns into the running summary."""
    if not pending:
        return None
    continues = last is not None and last.kind == "running" and pending[0].at - last.ended_at <= gap
    base = last if continues else None
    episode = leading_episode(pending, gap)
    closed = len(episode) < len(pending) or now - episode[-1].at > gap
    if closed:
        return Job("episode", tuple(episode), base)
    if len(episode) > COMPRESS_AT:
        return Job("running", tuple(episode[: len(episode) - KEEP_TURNS]), base)
    return None


def clean_text(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clean_profile(raw) -> dict:
    """A bounded profile from model output: short items, no duplicates, nothing sensitive."""
    raw = raw if isinstance(raw, dict) else {}
    name = clean_text(raw.get("name"), 20) or None
    profile = {"name": None if name and SENSITIVE.search(name) else name}
    for key in PROFILE_KEYS:
        items = raw.get(key) if isinstance(raw.get(key), list) else []
        kept: list[str] = []
        for item in items:
            text = clean_text(item, PROFILE_ITEM_MAX)
            if text and text not in kept and not SENSITIVE.search(text):
                kept.append(text)
        profile[key] = kept[:PROFILE_ITEMS]
    return profile


def empty_profile() -> dict:
    return {"name": None, **{key: [] for key in PROFILE_KEYS}}


def is_empty(profile: dict) -> bool:
    return not profile.get("name") and not any(profile.get(key) for key in PROFILE_KEYS)
