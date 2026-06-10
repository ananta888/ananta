from __future__ import annotations

from typing import Any

from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TeamBlueprintDB
from agent.services.repository_registry import get_repository_registry as _repos
from agent.services.team_definition_version_service import enrich_blueprint_payload


def _serialize_blueprint(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB] | None = None,
    artifacts: list[BlueprintArtifactDB] | None = None,
) -> dict:
    blueprint_dict = blueprint.model_dump()
    blueprint_roles = roles if roles is not None else _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = artifacts if artifacts is not None else _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    blueprint_dict["roles"] = [role.model_dump() for role in blueprint_roles]
    blueprint_dict["artifacts"] = [artifact.model_dump() for artifact in blueprint_artifacts]
    blueprint_dict["workflow"] = _serialize_blueprint_workflow(blueprint.id)
    return enrich_blueprint_payload(blueprint_dict, blueprint, blueprint_roles, blueprint_artifacts)


def _serialize_blueprint_workflow(blueprint_id: str) -> dict | None:
    repo = getattr(_repos(), "blueprint_workflow_step_repo", None)
    if repo is None:
        return None
    rows = list(repo.get_by_blueprint(blueprint_id) or [])
    if not rows:
        return None
    return {
        "mode": "gated",
        "default_failure_policy": "manual",
        "steps": [
            {
                "id": r.step_id,
                "role": r.role_name,
                "task_kind": r.task_kind,
                "title": r.title,
                "description": r.description,
                "produces": list(r.produces or []),
                "consumes": list(r.consumes or []),
            }
            for r in rows
        ],
    }
