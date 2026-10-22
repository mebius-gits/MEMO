"""Validate shipped Node-RED functions against the real API contract."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from memo.infrastructure.settings import Settings
from memo.main import create_app


def test_compose_and_flow(tmp_path, monkeypatch):
    compose = yaml.safe_load(Path("compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["node-red"]["depends_on"]["api"]["condition"] == "service_healthy"
    nodes = json.loads(Path("node-red/flows.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in nodes}
    assert len(ids) == len(nodes)
    for node in nodes:
        for output in node.get("wires", []):
            assert set(output) <= ids

    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("Node.js is needed to execute Node-RED function bodies")
    script = """
const fs = require('fs');
const nodes = JSON.parse(fs.readFileSync('node-red/flows.json', 'utf8'));
const env = {get: key => ({MEMO_API_URL:'http://api:8000', MEMO_DEVICE_ID:'room01'})[key]};
const requests = {};
const values = {};
const flow = {get: key => values[key], set: (key, value) => { values[key] = value; }};
for (const n of nodes.filter(n => n.type === 'function')) {
    const fn = new Function('msg', 'env', 'flow', n.func);
    requests[n.id] = fn({_msgid:'test-message'}, env, flow);
}
console.log(JSON.stringify(requests));
"""
    result = subprocess.run(
        [node_executable, "-e", script], check=True, capture_output=True, encoding="utf-8"
    )
    requests = json.loads(result.stdout)
    url = f"sqlite:///{(tmp_path / 'flow.db').as_posix()}"
    monkeypatch.setenv("BUDDY_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(Settings(database_url=url))) as client:
        for key, status in [
            ("health-build", 200),
            ("register-build", 201),
            ("register-build", 409),
            ("telemetry-build", 200),
            ("history-build", 200),
        ]:
            request = requests[key]
            response = client.request(
                request["method"],
                request["url"].removeprefix("http://api:8000"),
                json=request.get("payload"),
            )
            assert response.status_code == status, response.text
        assert response.json()[0]["temperature_c"] == 29.2
        request = requests["llm-build"]
        response = client.request(
            request["method"],
            request["url"].removeprefix("http://api:8000"),
            json=request["payload"],
        )
        assert response.status_code == 200
        assert response.json()["source"] == "template"
        assert response.json()["emotion"]["label"] == "friendly"
        assert "29.2" in response.json()["text"]
