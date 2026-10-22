import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client as mqtt
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Device
from memo.infrastructure.database import Base, build_engine
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.settings import MqttSettings
from memo.workers.environment import Delivery, EnvironmentWorker


@pytest.fixture
def worker(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'mqtt.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        EnvironmentService(SqlAlchemyRepository(session)).register_device(
            Device("room01", "Room", "")
        )
    client = Mock()
    client.ack.return_value = mqtt.MQTT_ERR_SUCCESS
    worker = EnvironmentWorker(MqttSettings(queue_size=1), sessions, client)
    worker.subscribed.set()
    yield worker
    engine.dispose()


def delivery(**changes):
    payload = {
        "message_id": "boot-1",
        "device_id": "room01",
        "sequence": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"temperature_c": 27.5},
    } | changes
    return Delivery(
        "buddy/v1/room01/telemetry",
        json.dumps(payload).encode(),
        1,
        1,
        False,
        0,
        datetime.now(timezone.utc),
    )


def history(worker):
    with worker.session_factory() as session:
        return EnvironmentService(SqlAlchemyRepository(session)).history(
            "room01", None, None, 100, 0
        )


def test_ack_only_after_storage_and_duplicate_is_idempotent(worker):
    message = delivery()

    def ack(mid, qos):
        assert len(history(worker)) == 1
        return mqtt.MQTT_ERR_SUCCESS

    worker.client.ack.side_effect = ack
    worker.process(message)
    worker.process(message)
    assert len(history(worker)) == 1
    assert worker.client.ack.call_count == 2


@pytest.mark.parametrize(
    "kind",
    ["invalid_json", "topic_mismatch", "unknown_device", "invalid_metric", "retained", "qos_zero"],
)
def test_terminal_errors_are_not_stored(worker, kind):
    message = delivery()
    if kind == "invalid_json":
        message = replace(message, payload=b"invalid")
    elif kind == "topic_mismatch":
        message = replace(message, topic="buddy/v1/another/telemetry")
    elif kind == "unknown_device":
        message = replace(delivery(device_id="unknown"), topic="buddy/v1/unknown/telemetry")
    elif kind == "invalid_metric":
        message = delivery(metrics={"humidity_pct": 200})
    elif kind == "retained":
        message = replace(message, retained=True)
    else:
        message = replace(message, qos=0)
    worker.process(message)
    assert history(worker) == []
    worker.client.ack.assert_called_once()


def test_storage_failure_does_not_ack(worker):
    worker.session_factory = Mock(side_effect=OperationalError("insert", {}, Exception("offline")))
    with pytest.raises(OperationalError):
        worker.process(delivery())
    worker.client.ack.assert_not_called()


def test_queue_full_requests_restart_without_ack(worker):
    message = delivery()
    wire = SimpleNamespace(topic=message.topic, payload=message.payload, mid=1, qos=1, retain=False)
    worker.on_message(worker.client, None, wire)
    worker.on_message(worker.client, None, wire)
    assert worker.restart.is_set()
    assert worker.inbox.qsize() == 1
    worker.client.ack.assert_not_called()


def test_old_connection_delivery_does_not_ack_new_packet_id(worker):
    worker.generation = 2
    worker.process(delivery())
    assert len(history(worker)) == 1
    worker.client.ack.assert_not_called()


def test_conflicting_duplicate_keeps_original(worker):
    worker.process(delivery())
    worker.process(delivery(metrics={"temperature_c": 99}))
    assert history(worker)[0].temperature_c == 27.5
    assert worker.client.ack.call_count == 2
