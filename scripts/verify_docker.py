"""Build/start the stack and verify real MQTT ingestion and worker restart.

Run: uv run python scripts/verify_docker.py
Leaves services running and retains its uniquely named smoke-test device/readings.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True)


def get_json(url):
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def wait_readings(device_id, minimum):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        readings = get_json(f"http://localhost:8000/api/v1/devices/{device_id}/readings")
        if len(readings) >= minimum:
            return readings
        time.sleep(0.5)
    raise RuntimeError(f"MQTT readings did not reach {minimum}")


def publish(device_id):
    compose(
        "run",
        "--rm",
        "--no-deps",
        "simulator",
        "python",
        "-m",
        "memo.workers.simulator",
        "--api-url",
        "http://api:8000",
        "--device-id",
        device_id,
        "--count",
        "3",
        "--interval",
        "0.2",
    )


def main():
    if shutil.which("docker") is None:
        raise SystemExit("Docker CLI not found. Install/start Docker Desktop (Linux containers).")
    compose("config", "--quiet")
    compose("up", "-d", "--build", "--wait", "--wait-timeout", "180")
    assert get_json("http://localhost:8000/health/ready")["status"] == "ready"
    flows = get_json("http://localhost:1880/flows")
    if isinstance(flows, dict):
        flows = flows["flows"]
    assert any(node.get("id") == "memo-flow" for node in flows), "Node-RED flow not loaded"
    device_id = "smoke-" + uuid4().hex[:12]
    publish(device_id)
    initial = wait_readings(device_id, 3)
    # Persistent broker session must queue messages while the worker is stopped.
    compose("stop", "environment-worker")
    try:
        publish(device_id)
    finally:
        compose("up", "-d", "--wait", "--wait-timeout", "90", "environment-worker")
    after = wait_readings(device_id, 6)
    assert {r["message_id"] for r in initial} <= {r["message_id"] for r in after}
    print(f"PASS: API, Node-RED, MQTT storage and offline worker recovery; device={device_id}")


if __name__ == "__main__":
    main()
