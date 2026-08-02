from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_enterprise_organization_release_gate import (
    GateConfigurationError,
    _validate_profile,
    evaluate_task_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_task_is_the_only_leaf_and_reaches_every_predecessor() -> None:
    todo = json.loads(
        (ROOT / "todos/todo.enterprise-agentic-scrum-organization-blueprints.json").read_text(encoding="utf-8")
    )

    result = evaluate_task_graph(todo, release_task_id="ESORG-QA-006")

    structural_reasons = {reason for reason in result["reason_codes"] if reason != "release_predecessors_incomplete"}
    assert structural_reasons == set()
    assert result["summary"]["leaves"] == ["ESORG-QA-006"]
    assert result["transitive_predecessor_count"] == 90


def test_release_profile_has_exactly_one_full_e2e() -> None:
    profile = json.loads(
        (ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json").read_text(encoding="utf-8")
    )

    suites = _validate_profile(profile)

    assert sum(suite["tier"] == "full_e2e" for suite in suites) == 1
    e2e = next(suite for suite in suites if suite["tier"] == "full_e2e")
    assert e2e["command"][-1] == "tests/enterprise-organization-medium-eight-team.spec.ts"


def test_profile_rejects_a_second_full_e2e() -> None:
    profile = json.loads(
        (ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json").read_text(encoding="utf-8")
    )
    profile["suites"].append({**profile["suites"][-1], "id": "second-e2e"})

    with pytest.raises(GateConfigurationError, match="exactly_one_full_e2e"):
        _validate_profile(profile)
