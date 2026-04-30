from __future__ import annotations

from agent.services.planning_proposal_service import (
    build_plan_proposal,
    normalize_planning_policy_config,
    select_planning_agent_candidate,
    validate_plan_proposal_payload,
)


def test_normalize_planning_policy_config_applies_bounds_and_defaults() -> None:
    policy = normalize_planning_policy_config({"max_nodes": 200, "max_depth": 0, "timeout_seconds": 1})
    assert policy["max_nodes"] == 50
    assert policy["max_depth"] == 1
    assert policy["timeout_seconds"] == 5
    assert "planning-agent" in policy["allowed_planner_roles"]


def test_select_planning_agent_candidate_prefers_allowed_local_roles() -> None:
    policy = normalize_planning_policy_config({"delegated_planning_enabled": True})
    selected = select_planning_agent_candidate(
        agents=[
            {
                "name": "remote",
                "url": "https://planner.example.com",
                "status": "online",
                "worker_roles": ["planning-agent"],
                "capabilities": ["plan.propose"],
            },
            {
                "name": "local",
                "url": "http://localhost:5002",
                "status": "online",
                "worker_roles": ["planning-agent"],
                "capabilities": ["plan.propose", "risk.estimate"],
            },
        ],
        planning_policy=policy,
    )
    assert selected is not None
    assert selected["name"] == "local"


def test_validate_plan_proposal_payload_rejects_duplicate_and_unknown_dependency() -> None:
    payload = {
        "plan_proposal_contract_version": "v1",
        "goal_id": "G1",
        "trace_id": "T1",
        "summary": "test",
        "nodes": [
            {"node_key": "N1", "title": "A", "description": "a", "task_kind": "coding", "depends_on": [], "required_capabilities": [], "risk_level": "medium"},
            {"node_key": "N1", "title": "B", "description": "b", "task_kind": "coding", "depends_on": ["N9"], "required_capabilities": [], "risk_level": "medium"},
        ],
    }
    result = validate_plan_proposal_payload(payload, known_capabilities={"coding"})
    assert result.ok is False
    assert any(item.startswith("duplicate_node_key:") for item in result.errors)
    assert any(item.startswith("unknown_dependency:") for item in result.errors)


def test_build_plan_proposal_and_validate_happy_path() -> None:
    payload = build_plan_proposal(
        goal_id="G-123",
        trace_id="TR-1",
        summary="Implement helper with tests",
        subtasks=[
            {"title": "Implement helper", "description": "code", "task_kind": "coding", "risk_level": "medium"},
            {"title": "Write tests", "description": "tests", "task_kind": "testing", "depends_on": ["1"], "risk_level": "high"},
        ],
    )
    result = validate_plan_proposal_payload(payload, known_capabilities={"coding", "testing"})
    assert result.ok is True
    assert result.errors == []
    assert len(result.normalized_payload["nodes"]) == 2
