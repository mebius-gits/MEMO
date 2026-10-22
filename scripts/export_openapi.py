"""Run with uv run python scripts/export_openapi.py."""

import json
from pathlib import Path

import yaml

from memo.main import create_app
from memo.presentation.interaction_schemas import ModelReply

root = Path(__file__).resolve().parents[1]
destination = root / "docs"
destination.mkdir(exist_ok=True)
schema = create_app().openapi()
(destination / "openapi.yaml").write_text(
    yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
(destination / "llm-output.schema.json").write_text(
    json.dumps(ModelReply.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print("Exported docs/openapi.yaml and docs/llm-output.schema.json")
