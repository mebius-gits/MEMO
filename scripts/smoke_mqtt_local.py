"""Real local MQTT transport smoke test without Docker.

npm --prefix scripts/mqtt-broker ci
uv run python scripts/smoke_mqtt_local.py
Uses an ephemeral Aedes broker, temporary SQLite, and the production worker.
This does not validate Docker or Mosquitto.
"""

import json
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from sqlalchemy.orm import sessionmaker

from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Device
from memo.infrastructure.database import Base, build_engine
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.settings import MqttSettings
from memo.workers.environment import EnvironmentWorker


def exercise(port, directory):
    engine = build_engine(f"sqlite:///{(directory / 'smoke.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        EnvironmentService(SqlAlchemyRepository(session)).register_device(
            Device("smoke", "Smoke", "")
        )
    settings = MqttSettings(host="127.0.0.1", port=port, client_id="smoke-environment-worker")
    worker = EnvironmentWorker(settings, sessions, heartbeat=directory / "worker.ready")
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smoke-publisher")
    try:
        assert worker.subscribed.wait(10), "Worker did not subscribe"
        publisher.connect("127.0.0.1", port)
        publisher.loop_start()

        def send(sequence):
            payload = json.dumps(
                {
                    "message_id": f"smoke-{sequence}",
                    "device_id": "smoke",
                    "sequence": sequence,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "metrics": {"temperature_c": 25 + sequence},
                }
            )
            for _ in range(2):
                info = publisher.publish("memo/v1/smoke/telemetry", payload, qos=1)
                info.wait_for_publish(5)
                assert info.is_published()

        def wait_for(count):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with sessions() as session:
                    rows = EnvironmentService(SqlAlchemyRepository(session)).history(
                        "smoke", None, None, 100, 0
                    )
                if len(rows) == count:
                    return
                time.sleep(0.1)
            raise AssertionError(f"Expected {count} stored readings")

        for sequence in range(1, 4):
            send(sequence)
        wait_for(3)
        worker.stop.set()
        thread.join(10)
        assert not thread.is_alive()
        send(4)
        worker = EnvironmentWorker(settings, sessions, heartbeat=directory / "worker.ready")
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        assert worker.subscribed.wait(10)
        wait_for(4)
        print("PASS: real MQTT QoS 1 -> worker -> SQLite, duplicates, offline session recovery")
    finally:
        worker.stop.set()
        thread.join(10)
        publisher.disconnect()
        publisher.loop_stop()
        engine.dispose()


def main():
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required for the local MQTT test broker")
    broker_script = Path(__file__).resolve().parent / "mqtt-broker" / "broker.mjs"
    broker = subprocess.Popen(
        [node, str(broker_script)],
        stdout=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        port_line = broker.stdout.readline().strip()
        if not port_line.isdigit():
            raise RuntimeError("Broker did not start; run npm --prefix scripts/mqtt-broker ci")
        with tempfile.TemporaryDirectory(prefix="memo-mqtt-") as directory:
            exercise(int(port_line), Path(directory))
    finally:
        broker.terminate()
        try:
            broker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.wait()


if __name__ == "__main__":
    main()
