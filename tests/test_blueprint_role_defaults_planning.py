from __future__ import annotations

from agent.routes.tasks.auto_planner import AutoPlanner  # noqa: F401
from agent.services.planning_service import get_planning_service


def test_role_defaults_enrich_capabilities_and_verification_spec() -> None:
    service = get_planning_service()
    nodes = service._build_nodes(
        "plan-role-defaults",
        [
            {
                "title": "Implement secure authentication flow",
                "description": "Code and test token refresh logic",
                "priority": "High",
                "blueprint_role_defaults": {
                    "capability_defaults": ["security_review", "testing"],
                    "risk_profile": "strict",
                    "verification_defaults": {"required": True, "policy": True, "gates": ["human_review"]},
                },
            }
        ],
        "template",
    )

    node = nodes[0]
    rationale = node.rationale or {}
    required_capabilities = list(rationale.get("required_capabilities") or [])
    assert "coding" in required_capabilities
    assert "security_review" in required_capabilities
    assert "testing" in required_capabilities
    assert rationale["blueprint_role_defaults"]["risk_profile"] == "strict"
    assert node.verification_spec["policy"] is True
    assert "human_review" in list(node.verification_spec.get("required_gates") or [])


def test_role_defaults_without_capability_defaults_keep_heuristic_capabilities() -> None:
    service = get_planning_service()
    nodes = service._build_nodes(
        "plan-role-defaults-fallback",
        [
            {
                "title": "Implement parser normalization",
                "description": "Create implementation and update docs",
                "priority": "Medium",
                "blueprint_role_defaults": {
                    "risk_profile": "balanced",
                    "verification_defaults": {"required": False},
                },
            }
        ],
        "template",
    )

    required_capabilities = list((nodes[0].rationale or {}).get("required_capabilities") or [])
    assert "coding" in required_capabilities
