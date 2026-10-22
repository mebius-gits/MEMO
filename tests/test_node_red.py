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
const env = {get: key => ({API_URL:'http://api:8000', DEVICE_ID:'room01', CORS_ORIGINS:'["http://localhost:5173"]'})[key]};
const requests = {};
const values = {};
const flow = {get: key => values[key], set: (key, value) => { values[key] = value; }};
const node = {status() {}, warn() {}};
const fns = {};
for (const n of nodes.filter(n => n.type === 'function')) {
    fns[n.id] = new Function('msg', 'env', 'flow', 'node', n.func);
    requests[n.id] = fns[n.id]({_msgid:'test-message'}, env, flow, node);
}
const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
const sample = i => fns['mqtt-build']({_msgid:'drift-' + i}, env, flow, node).payload;
const normal = Array.from({length: 500}, (_, i) => sample(i).metrics);
fns['mqtt-anomaly']({payload: JSON.parse(byId['mqtt-anomaly-co2'].payload)}, env, flow, node);
let raised; for (let i = 0; i < 20; i++) raised = sample('raised-' + i);
fns['mqtt-anomaly']({payload: JSON.parse(byId['mqtt-anomaly-reset'].payload)}, env, flow, node);
let recovered; for (let i = 0; i < 20; i++) recovered = sample('recovered-' + i);
const web = (method, payload, origin = 'http://localhost:5173') => fns['mqtt-anomaly']({req: {method, headers: {origin}}, payload}, env, flow, node);
const webSet = web('POST', JSON.stringify({offsets: {co2_ppm: 900, temperature_c: 99}, enabled: false}));
const stopped = fns['mqtt-gate']({}, env, flow, node);
const webBad = web('POST', 'not json');
const webForeign = web('GET', {}, 'https://evil.example');
const webReset = web('POST', JSON.stringify({reset: true, enabled: true}));
console.log(JSON.stringify({requests, normal, raised, recovered, webSet, stopped, webBad, webForeign, webReset}));
"""
    result = subprocess.run(
        [node_executable, "-e", script], check=True, capture_output=True, encoding="utf-8"
    )
    output = json.loads(result.stdout)
    requests = output["requests"]
    normal = output["normal"]
    # Drift varies but stays inside the normal band, below every alert threshold.
    for key, low, high in [("temperature_c", 23, 27.5), ("humidity_pct", 45, 68), ("co2_ppm", 550, 950)]:
        values = [metrics[key] for metrics in normal]
        assert low <= min(values) and max(values) <= high
        assert len(set(values)) > 10
    assert output["raised"]["metrics"]["co2_ppm"] > 1200
    assert output["recovered"]["metrics"]["co2_ppm"] <= 950
    # Web page endpoint: absolute offsets (clamped), start/stop, CORS only for configured origins.
    debug, response = output["webSet"]
    assert debug is None
    assert response["payload"]["anomaly_offset"] == {"co2_ppm": 900, "temperature_c": 15}
    assert response["payload"]["enabled"] is False and output["stopped"] is None
    assert response["headers"]["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert output["webBad"][1]["statusCode"] == 400
    assert output["webForeign"][1]["headers"] == {}
    assert output["webReset"][1]["payload"]["anomaly_offset"] == {}
    assert output["webReset"][1]["payload"]["enabled"] is True
    url = f"sqlite:///{(tmp_path / 'flow.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
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
        response = client.post("/api/v1/telemetry", json=output["raised"])
        assert response.status_code == 200, response.text
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
