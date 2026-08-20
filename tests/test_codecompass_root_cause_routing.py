from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from agent.services.codecompass_root_cause_routing import (
    build_conflict_set,
    evaluate_root_cause_routing,
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


def test_eligible_task_invokes_bounded_backend_port() -> None:
    seen = {}

    class Backend:
        def analyze(self, **kwargs):
            seen.update(kwargs)
            return {"usage": {"thinking_steps": 2, "tool_calls": 3, "tokens": 900}, "conflicts": ["open"]}

    result = route_root_cause(
        {"task_kind": "root_cause_investigation"},
        policy={"max_thinking_steps": 999, "max_tool_calls": 999, "max_tokens": 999999, "allow_moe_offload": True},
        backend=Backend(),
    )
    assert result["execution_status"] == "completed"
    assert seen["retrieval_profile"] == "evidence"
    assert seen["preserve_conflicts"] is True
    assert dict(seen["policy"])["max_tokens"] == 4000
    assert dict(seen["policy"])["allow_moe_offload"] is False
    assert evaluate_root_cause_routing(result)["budget_respected"] is True


def test_backend_cannot_report_usage_above_hard_budget() -> None:
    class Backend:
        def analyze(self, **_kwargs):
            return {"usage": {"thinking_steps": 7, "tool_calls": 0, "tokens": 0}}

    import pytest

    with pytest.raises(ValueError, match="root_cause_budget_exceeded"):
        route_root_cause({"task_kind": "root_cause_investigation"}, backend=Backend())
