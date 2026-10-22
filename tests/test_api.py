import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.infrastructure.settings import Settings
from memo.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("MEMO_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    return Settings(database_url=url, _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def register(client):
    return client.post("/api/v1/devices", json={"id": "room01", "name": "Room"})


def payload(**changes):
    data = {
        "message_id": "room01-boot1-1",
        "device_id": "room01",
        "sequence": 1,
        "observed_at": "2026-10-22T12:00:00+08:00",
        "metrics": {"temperature_c": 29.2, "humidity_pct": 65, "co2_ppm": 1100},
    }
    return data | changes


def test_device_lifecycle(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert client.get("/api/v1/devices").json()[0]["id"] == "room01"
    assert client.get("/api/v1/devices/room01").status_code == 200
    assert client.get("/api/v1/devices/missing").status_code == 404


def test_ingestion_retry_conflict_and_restart(client, settings):
    register(client)
    first = client.post("/api/v1/telemetry", json=payload())
    assert first.status_code == 200
    retry = client.post("/api/v1/telemetry", json=payload())
    assert retry.json() == first.json()
    assert client.post("/api/v1/telemetry", json=payload(sequence=2)).status_code == 409
    with TestClient(create_app(settings)) as restarted:
        history = restarted.get("/api/v1/devices/room01/readings").json()
        assert len(history) == 1
        assert history[0]["observed_at"] == "2026-10-22T04:00:00Z"


def test_history_order_filter_and_missing_values(client):
    register(client)
    client.post("/api/v1/telemetry", json=payload())
    older = payload(
        message_id="older", observed_at="2026-10-22T03:00:00Z", metrics={"temperature_c": 20}
    )
    assert client.post("/api/v1/telemetry", json=older).status_code == 200
    path = "/api/v1/devices/room01/readings"
    rows = client.get(path).json()
    assert [r["message_id"] for r in rows] == ["room01-boot1-1", "older"]
    assert rows[1]["humidity_pct"] is None
    assert len(client.get(path, params={"start": "2026-10-22T03:30:00Z"}).json()) == 1
    assert client.get(path, params={"limit": 1, "offset": 1}).json()[0] == rows[1]
    assert (
        client.get(
            path, params={"start": "2026-10-23T00:00:00Z", "end": "2026-10-22T00:00:00Z"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": "2026-10-22T12:00:00"},
        {"metrics": {}},
        {"metrics": {"humidity_pct": 101}},
        {"schema_version": 2},
        {"sequence": -1},
        {"unexpected": True},
    ],
)
def test_reject_invalid_payload(client, changes):
    register(client)
    assert client.post("/api/v1/telemetry", json=payload(**changes)).status_code == 422
    assert client.get("/api/v1/devices/room01/readings").json() == []


def test_unknown_device(client):
    assert client.post("/api/v1/telemetry", json=payload()).status_code == 404


def test_readiness_without_migrations(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_migration_roundtrip(settings):
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
