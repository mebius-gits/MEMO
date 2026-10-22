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
    monkeypatch.setenv("BUDDY_DATABASE_URL", url)
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
    response = client.post("/api/v1/devices/web/interaction", json={"user_message": "環境如何？"})
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
