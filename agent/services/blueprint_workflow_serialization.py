"""Canonical serializer for the complete persisted Team Blueprint workflow."""

from __future__ import annotations

from agent.db_models import BlueprintWorkflowStepDB, TeamBlueprintDB


def serialize_persisted_workflow(
    blueprint: TeamBlueprintDB,
    rows: list[BlueprintWorkflowStepDB],
) -> dict | None:
    if not rows:
        return None
    return {
        "definition_ref": (
            f"{blueprint.workflow_definition_key}@{blueprint.workflow_definition_version}"
            if blueprint.workflow_definition_key and blueprint.workflow_definition_version
            else None
        ),
        "mode": blueprint.workflow_mode,
        "default_failure_policy": blueprint.workflow_default_failure_policy,
        "checks": dict(blueprint.workflow_checks or {}),
        "required_capabilities": list(blueprint.workflow_required_capabilities or []),
        "steps": [
            {
                "id": row.step_id,
                "role": row.role_name,
                "task_kind": row.task_kind,
                "title": row.title,
                "description": row.description,
                "produces": list(row.produces or []),
                "consumes": list(row.consumes or []),
                "depends_on": list(row.depends_on or []),
                "gate": bool(row.gate),
                "checks": dict(row.checks or {}),
                "failure_policy": row.failure_policy,
                "required_capabilities": list(row.required_capabilities or []),
                "sort_order": int(row.sort_order),
                "pattern_hints": dict(row.pattern_hints) if row.pattern_hints else None,
            }
            for row in sorted(rows, key=lambda item: (item.sort_order, item.step_id))
        ],
    }


__all__ = ["serialize_persisted_workflow"]
