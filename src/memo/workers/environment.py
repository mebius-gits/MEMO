"""MQTT QoS 1 ingestion with bounded queue and acknowledgement after commit."""

import argparse
import logging
import queue
import signal
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Conflict, NotFound
from memo.infrastructure.database import build_engine
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.settings import MqttSettings, Settings
from memo.presentation.schemas import TelemetryInput
from memo.presentation.telemetry import to_reading

logger = logging.getLogger(__name__)
HEARTBEAT = Path(tempfile.gettempdir()) / "memo-environment-worker.ready"


@dataclass(frozen=True)
class Delivery:
    topic: str
    payload: bytes
    mid: int
    qos: int
    retained: bool
    generation: int
    received_at: datetime


def decode(delivery: Delivery) -> TelemetryInput:
    if delivery.qos != 1 or delivery.retained:
        raise ValueError("telemetry requires QoS 1 and retain=false")
    payload = TelemetryInput.model_validate_json(delivery.payload)
    if delivery.topic != f"memo/v1/{payload.device_id}/telemetry":
        raise ValueError("topic does not match payload device_id")
    return payload


class EnvironmentWorker:
    def __init__(self, settings: MqttSettings, session_factory, client=None, heartbeat=HEARTBEAT):
        self.settings = settings
        self.session_factory = session_factory
        self.heartbeat = heartbeat
        self.inbox = queue.Queue(maxsize=settings.queue_size)
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.subscribed = threading.Event()
        self.generation = 0
        self.client = client or mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=True,
        )
        if settings.username:
            self.client.username_pw_set(settings.username, settings.password)
        if settings.tls:
            self.client.tls_set()
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("MQTT connection rejected: %s", reason_code)
            return
        self.generation += 1
        self.subscribed.clear()
        result, _ = client.subscribe(self.settings.topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self.restart.set()
        logger.info("MQTT connected; subscribing to %s", self.settings.topic)

    def on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if not reason_codes or any(code.is_failure for code in reason_codes):
            logger.error("MQTT subscription rejected")
            self.restart.set()
            return
        self.subscribed.set()
        logger.info("MQTT subscription ready")

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.subscribed.clear()
        logger.warning("MQTT disconnected: %s", reason_code)

    def on_message(self, client, userdata, message):
        # No database work here: keep the network loop responsive.
        if len(message.payload) > self.settings.max_payload_bytes:
            logger.warning("Rejected oversized MQTT payload")
            client.ack(message.mid, message.qos)
            return
        delivery = Delivery(
            message.topic,
            message.payload,
            message.mid,
            message.qos,
            message.retain,
            self.generation,
            datetime.now(timezone.utc),
        )
        try:
            self.inbox.put_nowait(delivery)
        except queue.Full:
            # Leave QoS 1 unacknowledged; broker redelivers after process restart.
            logger.error("MQTT queue full; restarting without acknowledging queued messages")
            self.restart.set()

    def process(self, delivery: Delivery):
        try:
            payload = decode(delivery)
            with self.session_factory() as session:
                service = EnvironmentService(SqlAlchemyRepository(session))
                service.ingest(to_reading(payload, delivery.received_at))
            logger.info(
                "Stored telemetry message_id=%s device_id=%s", payload.message_id, payload.device_id
            )
        except (ValidationError, ValueError, NotFound, Conflict) as exc:
            # Invalid messages are terminal: log and acknowledge to avoid poison loops.
            logger.warning("Rejected telemetry: %s", type(exc).__name__)
        # Operational/storage errors propagate: never acknowledge failed commits.
        if delivery.generation == self.generation and self.subscribed.is_set():
            result = self.client.ack(delivery.mid, delivery.qos)
            if result != mqtt.MQTT_ERR_SUCCESS:
                self.restart.set()

    def run(self):
        self.heartbeat.unlink(missing_ok=True)
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=30)
        self.client.loop_start()
        pending = None
        retry_delay = 1
        try:
            while not self.stop.is_set() and not self.restart.is_set():
                if self.subscribed.is_set():
                    self.heartbeat.touch()
                else:
                    self.heartbeat.unlink(missing_ok=True)
                if pending is None:
                    try:
                        pending = self.inbox.get(timeout=0.5)
                    except queue.Empty:
                        continue
                try:
                    self.process(pending)
                    self.inbox.task_done()
                    pending = None
                    retry_delay = 1
                except OperationalError:
                    logger.warning("Database unavailable; retaining unacknowledged message")
                    self.heartbeat.unlink(missing_ok=True)
                    self.stop.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
        finally:
            self.client.disconnect()
            self.client.loop_stop()
            self.heartbeat.unlink(missing_ok=True)
        return 1 if self.restart.is_set() else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    if args.healthcheck:
        try:
            return 0 if time.time() - HEARTBEAT.stat().st_mtime < 15 else 1
        except FileNotFoundError:
            return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = build_engine(Settings().database_url)
    worker = EnvironmentWorker(MqttSettings(), sessionmaker(bind=engine, expire_on_commit=False))
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: worker.stop.set())
    try:
        return worker.run()
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
