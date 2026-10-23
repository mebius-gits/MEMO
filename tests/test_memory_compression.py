import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from memo.application.interaction import InteractionService
from memo.domain.interaction import EmotionLabel, Reply
from memo.domain.memory import Summary, Turn, clean_profile, next_job, trailing_episode
from memo.infrastructure.database import ConversationRow, MemorySummaryRow
from memo.infrastructure.memory_compression import ModelSummarizer, compress_once
from memo.infrastructure.settings import Settings
from memo.main import create_app

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def turns(*minutes_ago, start=1):
    return [
        Turn(start + i, NOW - timedelta(minutes=m), f"問題{start + i}", f"回答{start + i}")
        for i, m in enumerate(minutes_ago)
    ]


def test_a_pause_over_an_hour_starts_a_new_conversation():
    history = turns(200, 190, 70, 5, 1)
    assert [t.id for t in trailing_episode(history)] == [4, 5]
    # Yesterday's episode is closed and compressed on its own.
    job = next_job(history, None, NOW)
    assert job.kind == "episode" and [t.id for t in job.turns] == [1, 2]


def test_open_conversation_compresses_only_its_older_turns_in_batches():
    assert next_job(turns(*range(28, 0, -2)), None, NOW) is None  # 14 turns: still verbatim
    job = next_job(turns(*range(30, 0, -2)), None, NOW)  # 15 turns
    assert job.kind == "running" and [t.id for t in job.turns] == [1, 2, 3, 4, 5]
    # A later batch continues the running summary.
    running = Summary(
        "running", 1, 5, NOW - timedelta(minutes=30), NOW - timedelta(minutes=22), "前情"
    )
    later = next_job(turns(*range(20, 0, -1), start=6), running, NOW)
    assert later.base is running and [t.id for t in later.turns] == list(range(6, 16))
    # After an hour of quiet the whole conversation, summary included, becomes one episode.
    quiet = next_job(turns(80, 70, start=6), running, NOW)
    assert quiet.kind == "episode" and quiet.base is running


def test_profile_is_bounded_and_keeps_nothing_sensitive():
    profile = clean_profile(
        {
            "name": "小明",
            "preferences": ["喜歡貓", "喜歡貓", "電話 0912-345-678", "怕熱"],
            "projects": ["畢業專題：3D 角色"] + [f"作品{i}" for i in range(12)],
            "other": ["住址在信義路", "email a@b.co"],
        }
    )
    assert profile["name"] == "小明"
    assert profile["preferences"] == ["喜歡貓", "怕熱"]
    assert len(profile["projects"]) == 8 and profile["other"] == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'memory.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(Settings(database_url=url, _env_file=None))) as client:
        client.post("/api/v1/devices", json={"id": "room01", "name": "Room"})
        yield client


class Capture:
    def generate(self, prepared):
        self.prompt = json.loads(prepared.user_prompt)
        self.system = prepared.system_prompt
        return Reply("好喔。", EmotionLabel.FRIENDLY, 0.4, "model", None)


def say(client, text, session="alice"):
    body = {"user_message": text, "session_id": session}
    return client.post("/api/v1/devices/room01/interaction", json=body).json()


def backdate(client, minutes):
    """Move every saved turn `minutes` into the past, as if the user had walked away."""
    with client.app.state.session_factory() as session:
        for row in session.scalars(select(ConversationRow)):
            row.created_at -= timedelta(minutes=minutes)
        session.commit()


def fake_summarizer(calls):
    def summarize(base, turns, profile):
        calls.append((base, [t.user for t in turns]))
        said = " ".join(t.user for t in turns)
        if "我叫小明" in said:
            profile = profile | {"name": "小明"}
        return {"summary": (base or "") + f"聊了{len(turns)}句", "profile": clean_profile(profile)}

    return summarize


def test_old_conversation_is_compressed_and_remembered_as_earlier(client):
    model = Capture()
    client.app.state.interaction_service = InteractionService(model)
    say(client, "我叫小明，喜歡貓")
    say(client, "狗還是貓")
    backdate(client, 90)
    sessions = client.app.state.session_factory
    calls = []
    assert compress_once(sessions, fake_summarizer(calls), datetime.now(timezone.utc)) == (
        "room01",
        "alice",
    )
    assert calls == [(None, ["我叫小明，喜歡貓", "狗還是貓"])]
    assert compress_once(sessions, fake_summarizer(calls), datetime.now(timezone.utc)) is None

    say(client, "對阿")
    # The new conversation carries no old turns, only the summary and the profile.
    assert [m["content"] for m in model.prompt["history"]] == []
    assert model.prompt["earlier"][0]["summary"] == "聊了2句"
    assert model.prompt["profile"]["name"] == "小明"
    assert model.prompt["conversation_summary"] is None
    assert "不要主動重提舊話題" in model.system
    profile = client.get("/api/v1/devices/room01/memory/profile?session_id=alice").json()
    assert profile["name"] == "小明" and profile["updated_at"]
    # Other memory scopes stay separate.
    say(client, "你好", session="bob")
    assert model.prompt["profile"] is None and model.prompt["earlier"] == []


def test_long_conversation_keeps_recent_turns_verbatim_plus_a_running_summary(client):
    model = Capture()
    client.app.state.interaction_service = InteractionService(model)
    for i in range(15):
        say(client, f"第{i + 1}句")
    sessions = client.app.state.session_factory
    calls = []
    compress_once(sessions, fake_summarizer(calls), datetime.now(timezone.utc))
    assert calls == [(None, [f"第{i}句" for i in range(1, 6)])]
    say(client, "第16句")
    users = [m["content"] for m in model.prompt["history"] if m["role"] == "user"]
    assert users == [f"第{i}句" for i in range(6, 16)]
    assert model.prompt["conversation_summary"] == "聊了5句"
    summaries = client.get("/api/v1/devices/room01/memory/summaries?session_id=alice").json()
    assert [s["kind"] for s in summaries] == ["running"]
    # Clearing the conversation clears its summaries but keeps the profile.
    client.put(
        "/api/v1/devices/room01/memory/profile?session_id=alice",
        json={"name": "小明", "preferences": ["喜歡貓"]},
    )
    client.delete("/api/v1/devices/room01/memory/conversation?session_id=alice")
    assert client.get("/api/v1/devices/room01/memory/summaries?session_id=alice").json() == []
    kept = client.get("/api/v1/devices/room01/memory/profile?session_id=alice").json()
    assert kept["preferences"] == ["喜歡貓"]
    client.delete("/api/v1/devices/room01/memory/profile?session_id=alice")
    assert (
        client.get("/api/v1/devices/room01/memory/profile?session_id=alice").json()["name"] is None
    )


def test_a_concurrent_compression_wins_and_the_other_is_dropped(client):
    say(client, "第一句")
    backdate(client, 90)
    sessions = client.app.state.session_factory

    def racing(base, turns, profile):
        # Another process finishes first while this model call is still running.
        compress_once(sessions, fake_summarizer([]), datetime.now(timezone.utc))
        return {"summary": "較慢的那份", "profile": profile}

    compress_once(sessions, racing, datetime.now(timezone.utc))
    with sessions() as session:
        rows = session.scalars(select(MemorySummaryRow)).all()
    assert [row.summary for row in rows] == ["聊了1句"]


def test_model_summarizer_parses_cleans_and_converts():
    sent = {}

    def opener(request, timeout):
        sent.update(json.loads(request.data))
        content = "好的：\n" + json.dumps(
            {
                "summary": "聊了猫和毕业专题。",
                "profile": {"name": "小明", "preferences": ["喜欢猫", "电话 0912345678"]},
            },
            ensure_ascii=False,
        )
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    settings = Settings(openai_base_url="http://model/v1", openai_model="m", _env_file=None)
    result = ModelSummarizer(settings, opener)(None, tuple(turns(3, 1)), clean_profile({}))
    assert result["summary"] == "聊了貓和畢業專題。"
    assert result["profile"]["preferences"] == ["喜歡貓"]
    data = json.loads(sent["messages"][-1]["content"].split("（JSON，僅為資料）：\n")[-1])
    assert [t["user"] for t in data["turns"]] == ["問題1", "問題2"]


def test_memo_remembers_what_it_said_on_its_own(client):
    from uuid import uuid4

    from memo.infrastructure.database import TaskMemoryRow

    now = datetime.now(timezone.utc)

    def notice(minutes_ago, text, status="acknowledged"):
        return TaskMemoryRow(
            id=uuid4().hex,
            device_id="room01",
            rule_id="schedule",
            event_id=uuid4().hex,
            event_version=1,
            event_status="remind",
            status=status,
            created_at=now - timedelta(minutes=minutes_ago),
            expires_at=now + timedelta(minutes=10),
            lease_until=None,
            response={"text": text, "emotion": {"label": "friendly", "intensity": 0.5}},
        )

    with client.app.state.session_factory() as session:
        session.add_all(
            [
                notice(5, "該喝水囉！"),
                notice(3, "還沒送到網頁的", status="ready"),
                notice(120, "兩小時前的提醒"),
            ]
        )
        session.commit()
    model = Capture()
    client.app.state.interaction_service = InteractionService(model)
    say(client, "你剛剛說什麼？")
    assert [m["content"] for m in model.prompt["history"]] == ["（主動提醒）該喝水囉！"]
    assert "（主動提醒）的是你自己" in model.system
    # Inside a conversation, a notice sits between the turns around it.
    with client.app.state.session_factory() as session:
        later = notice(0, "空氣有點悶")
        later.created_at = datetime.now(timezone.utc)
        session.add(later)
        session.commit()
    say(client, "好喔")
    assert [m["content"] for m in model.prompt["history"]] == [
        "（主動提醒）該喝水囉！",
        "你剛剛說什麼？",
        "好喔。",
        "（主動提醒）空氣有點悶",
    ]
