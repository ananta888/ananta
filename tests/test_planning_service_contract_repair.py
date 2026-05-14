from __future__ import annotations

from agent.services.planning_proposal_service import validate_plan_proposal_payload


def test_missing_expected_artifacts_are_reported_for_repair() -> None:
    payload = {
        "plan_proposal_contract_version": "v1",
        "goal_id": "G2",
        "trace_id": "T2",
        "summary": "test",
        "goal_contract_requirements": {"requires_artifact_expectations": True},
        "nodes": [
            {
                "node_key": "N1",
                "title": "Code",
                "description": "code",
                "task_kind": "coding",
                "depends_on": [],
                "required_capabilities": [],
                "risk_level": "medium",
                "expected_artifacts": [],
            }
        ],
    }
    result = validate_plan_proposal_payload(payload, known_capabilities={"coding"})
    assert result.ok is False
    assert any(item.startswith("missing_expected_artifacts:") for item in result.errors)
