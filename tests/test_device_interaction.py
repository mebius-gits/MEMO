from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.infrastructure.settings import Settings
from memo.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'web.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(database_url=url, cors_origins=["https://memo.example"])
    with TestClient(create_app(settings)) as client:
        client.post("/api/v1/devices", json={"id": "web", "name": "Web"})
        yield client


@pytest.mark.parametrize("age,warning", [(0, []), (60, ["stale_observation"])])
def test_reply_uses_stored_observation(client, age, warning):
    client.post(
        "/api/v1/telemetry",
        json={
            "message_id": "web-1",
            "device_id": "web",
            "sequence": 1,
            "observed_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
            "metrics": {"temperature_c": 26.5},
        },
    )
    response = client.post(
        "/api/v1/devices/web/interaction", json={"user_message": "目前數值多少？"}
    )
    assert response.status_code == 200
    reply = response.json()
    assert reply["context_source"] == "stored_readings"
    assert reply["warnings"] == warning
    assert ("26.5" in reply["text"]) == (not warning)


def test_missing_and_forged_context(client):
    assert (
        client.post("/api/v1/devices/missing/interaction", json={"user_message": "Hi"}).status_code
        == 404
    )
    reply = client.post("/api/v1/devices/web/interaction", json={"user_message": "Hi"}).json()
    assert reply["warnings"] == ["missing_observation"]
    assert (
        client.post(
            "/api/v1/devices/web/interaction",
            json={
                "user_message": "Hi",
                "context": {"temperature_c": 10},
            },
        ).status_code
        == 422
    )


def test_cors_explicit_origins_only(client):
    for origin, code in [("https://memo.example", 200), ("https://other.example", 400)]:
        response = client.options(
            "/api/v1/devices/web/interaction",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == code
        assert (response.headers.get("access-control-allow-origin") == origin) == (code == 200)


def test_dialogue_scoped_persistent_idempotent_and_separate_from_tasks(client):
    from uuid import uuid4

    from memo.domain.interaction import EmotionLabel, Reply

    captured = []

    class Generator:
        def generate(self, prepared):
            captured.append(prepared.input)
            return Reply("我記得你剛才說的話。", EmotionLabel.FRIENDLY, 0.4, "model", None)

    client.app.state.interaction_service.generator = Generator()
    request_id = str(uuid4())
    body = {"session_id": "alice", "request_id": request_id, "user_message": "我叫小明"}
    first = client.post("/api/v1/devices/web/interaction", json=body)
    assert first.status_code == 200
    assert client.post("/api/v1/devices/web/interaction", json=body).json() == first.json()
    assert len(captured) == 1
    assert (
        client.post(
            "/api/v1/devices/web/interaction", json=body | {"user_message": "different"}
        ).status_code
        == 409
    )
    client.post(
        "/api/v1/devices/web/interaction", json={"session_id": "alice", "user_message": "記得我嗎"}
    )
    assert captured[-1].history[0]["content"] == "我叫小明"
    assert captured[-1].task_memory == ()
    client.post("/api/v1/devices/web/interaction", json={"session_id": "bob", "user_message": "Hi"})
    assert captured[-1].history == ()
    client.post("/api/v1/devices", json={"id": "other", "name": "Other"})
    client.post(
        "/api/v1/devices/other/interaction", json={"session_id": "alice", "user_message": "Hi"}
    )
    assert captured[-1].history == ()
    url = str(client.app.state.session_factory.kw["bind"].url)
    with TestClient(
        create_app(Settings(database_url=url, notifications_enabled=False))
    ) as restarted:
        turns = restarted.get("/api/v1/devices/web/memory/conversation?session_id=alice").json()
        assert len(turns) == 2
        assert restarted.get("/api/v1/devices/web/memory/tasks").json() == []
        assert (
            restarted.delete("/api/v1/devices/web/memory/conversation?session_id=alice").status_code
            == 204
        )
        assert (
            restarted.get("/api/v1/devices/web/memory/conversation?session_id=alice").json() == []
        )
        assert (
            len(restarted.get("/api/v1/devices/web/memory/conversation?session_id=bob").json()) == 1
        )


def test_normal_chat_does_not_recite_measurements(client):
    client.post(
        "/api/v1/telemetry",
        json={
            "message_id": "natural",
            "device_id": "web",
            "sequence": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {"temperature_c": 26.5, "humidity_pct": 60, "co2_ppm": 800},
        },
    )
    result = client.post(
        "/api/v1/devices/web/interaction", json={"user_message": "現在環境如何？"}
    ).json()
    assert "26.5" not in result["text"] and "800" not in result["text"]
    assert "?" not in result["text"]


def test_configured_model_is_called_over_http(client):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            content = json.dumps(
                {
                    "text": "你好，我會記得這段對話。",
                    "emotion": {"label": "friendly", "intensity": 0.4},
                }
            )
            output = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = str(client.app.state.session_factory.kw["bind"].url)
        settings = Settings(
            database_url=url,
            llm_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            llm_model="test-model",
            notifications_enabled=False,
        )
        with TestClient(create_app(settings)) as configured:
            result = configured.post(
                "/api/v1/devices/web/interaction",
                json={"session_id": "http-test", "user_message": "我今天想安靜一下"},
            )
            assert result.status_code == 200
            assert result.json()["source"] == "model"
            assert result.json()["fallback_reason"] is None
            assert captured[0][0] == "/v1/chat/completions"
            assert captured[0][1]["model"] == "test-model"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
