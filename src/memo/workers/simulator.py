"""Publish demo telemetry every five seconds; register device through the API."""

import argparse
import json
import logging
import signal
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import paho.mqtt.client as mqtt

from memo.infrastructure.settings import MqttSettings
from memo.presentation.schemas import DeviceSchema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--device-id", default="room01")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--count", type=int, default=0, help="0 means continuous")
    args = parser.parse_args()
    if args.interval <= 0 or args.count < 0:
        parser.error("interval must be positive and count must be nonnegative")
    logging.basicConfig(level=logging.INFO)
    device = DeviceSchema(id=args.device_id, name="MEMO MQTT simulator", location="demo")
    request = Request(
        args.api_url.rstrip("/") + "/api/v1/devices",
        data=device.model_dump_json().encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10):
            pass
    except HTTPError as exc:
        if exc.code != 409:
            raise
    settings = MqttSettings()
    boot_id = uuid4().hex
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"memo-sim-{boot_id}")
    if settings.username:
        client.username_pw_set(settings.username, settings.password)
    if settings.tls:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    stop = threading.Event()
    connected = threading.Event()

    def on_connect(client, userdata, flags, reason, properties):
        if not reason.is_failure:
            connected.set()

    client.on_connect = on_connect
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    client.connect(settings.host, settings.port, keepalive=30)
    client.loop_start()
    try:
        if not connected.wait(10):
            raise RuntimeError("MQTT connection did not complete")
        sequence = 0
        while not stop.is_set() and (args.count == 0 or sequence < args.count):
            sequence += 1
            payload = {
                "schema_version": 1,
                "message_id": f"{boot_id}-{sequence}",
                "device_id": device.id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "sequence": sequence,
                "quality": "ok",
                "metrics": {
                    "temperature_c": 25 + sequence % 10 / 10,
                    "humidity_pct": 60,
                    "co2_ppm": 800,
                },
            }
            info = client.publish(
                f"memo/v1/{device.id}/telemetry", json.dumps(payload), qos=1, retain=False
            )
            info.wait_for_publish(timeout=10)
            if not info.is_published():
                raise RuntimeError("MQTT publish was not acknowledged by broker")
            logging.info("Published %s", payload["message_id"])
            if args.count == 0 or sequence < args.count:
                stop.wait(args.interval)
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
