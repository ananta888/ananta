from __future__ import annotations

from agent.routes.tasks.auto_planner import AutoPlanner  # noqa: F401
from agent.services.planning_service import get_planning_service


def test_build_nodes_persists_blueprint_provenance_fields_in_rationale() -> None:
    service = get_planning_service()
    nodes = service._build_nodes(
        "plan-provenance",
        [
            {
                "title": "Implement API endpoint",
                "description": "Create endpoint with tests",
                "priority": "High",
                "blueprint_id": "bp-1",
                "blueprint_name": "TDD",
                "blueprint_artifact_id": "artifact-1",
                "blueprint_role_name": "Implementer",
                "template_name": "TDD Implementer Template",
                "template_id": "tmpl-1",
                "blueprint_role_template_hints": [
                    {
                        "role_name": "Implementer",
                        "template_id": "tmpl-1",
                        "template_name": "TDD Implementer Template",
                    }
                ],
            }
        ],
        "template",
    )

    rationale = nodes[0].rationale or {}
    assert rationale["blueprint_id"] == "bp-1"
    assert rationale["blueprint_name"] == "TDD"
    assert rationale["blueprint_artifact_id"] == "artifact-1"
    assert rationale["blueprint_role_name"] == "Implementer"
    assert rationale["template_name"] == "TDD Implementer Template"
    assert rationale["template_id"] == "tmpl-1"


def test_build_nodes_keeps_catalog_template_metadata_without_blueprint() -> None:
    service = get_planning_service()
    nodes = service._build_nodes(
        "plan-template-only",
        [
            {
                "title": "Write regression tests",
                "description": "Add tests for parser edge cases",
                "priority": "Medium",
                "template_id": "bug_fix",
                "template_name": "Bug Fix",
            }
        ],
        "template",
    )

    rationale = nodes[0].rationale or {}
    assert rationale["template_id"] == "bug_fix"
    assert rationale["template_name"] == "Bug Fix"
    assert "blueprint_id" not in rationale
