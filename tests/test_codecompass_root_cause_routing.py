from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from agent.services.codecompass_root_cause_routing import (
    build_conflict_set,
    route_root_cause,
)


def test_non_root_cause_falls_back() -> None:
    result = route_root_cause({"task_kind": "bugfix"})
    assert result["routed"] is False
    assert result["fallback"] == "hybrid_retrieval"


def test_root_cause_preserves_conflicts() -> None:
    result = route_root_cause({"task_kind": "root_cause_investigation"})
    assert result["routed"] is True
    assert result["preserve_conflicts"] is True
    assert result["policy"]["max_thinking_steps"] == 6
    payload = build_conflict_set([("docs say X", "code says Y")])
    schema = json.loads(Path("schemas/codecompass.conflict-set.v1.json").read_text())
    jsonschema.validate(payload, schema)
    assert payload["conflicts"][0]["status"] == "unresolved"
