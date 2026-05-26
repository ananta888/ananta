from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "artifacts" / "goal_artifact_graph.v1.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_goal_artifact_graph_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def build_empty_goal_artifact_graph(*, goal_id: str, graph_id: str | None = None) -> dict[str, Any]:
    now = _now_iso()
    graph = {
        "schema": "goal_artifact_graph.v1",
        "graph_id": graph_id or f"gag-{goal_id}",
        "goal_id": goal_id,
        "created_at": now,
        "updated_at": now,
        "source_grants": [],
        "source_usages": [],
        "output_artifacts": [],
        "edges": [],
        "extensions": {},
    }
    errors = validate_goal_artifact_graph_payload(graph)
    if errors:
        raise ValueError(f"invalid_goal_artifact_graph:{'; '.join(errors)}")
    return graph
