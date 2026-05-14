from __future__ import annotations

from agent.services.planning_service import PlanningService


def test_repair_invalid_plan_payload_adds_expected_artifacts() -> None:
    payload = {
        "nodes": [
            {
                "node_key": "N1",
                "task_kind": "coding",
                "expected_artifacts": [],
                "verification_spec": {},
            }
        ]
    }
    repaired = PlanningService._repair_invalid_plan_payload(
        payload,
        ["missing_expected_artifacts:N1"],
    )
    node = repaired["nodes"][0]
    assert node["expected_artifacts"]
    assert node["verification_spec"]["expected_artifacts"]

