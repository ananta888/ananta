from __future__ import annotations

import hashlib
import json
from typing import Any

from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TeamBlueprintDB, TeamDB, TemplateDB
from agent.services.repository_registry import get_repository_registry


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _revision(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:16]


def template_revision_payload(template: TemplateDB) -> dict[str, Any]:
    return {
        "name": template.name,
        "description": template.description,
        "prompt_template": template.prompt_template,
    }


def template_version_metadata(template: TemplateDB) -> dict[str, Any]:
    return {
        "revision": _revision(template_revision_payload(template)),
        "version_scheme": "content-sha256-16",
        "origin_kind": "template",
    }


def serialize_template_with_version(template: TemplateDB) -> dict[str, Any]:
    payload = template.model_dump()
    payload["version_metadata"] = template_version_metadata(template)
    return payload


def blueprint_revision_payload(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    return {
        "name": blueprint.name,
        "description": blueprint.description,
        "base_team_type_name": blueprint.base_team_type_name,
        "is_seed": blueprint.is_seed,
        "roles": [
            {
                "name": role.name,
                "description": role.description,
                "template_id": role.template_id,
                "sort_order": role.sort_order,
                "is_required": role.is_required,
                "config": role.config or {},
            }
            for role in sorted(roles, key=lambda item: (item.sort_order, item.name))
        ],
        "artifacts": [
            {
                "kind": artifact.kind,
                "title": artifact.title,
                "description": artifact.description,
                "sort_order": artifact.sort_order,
                "payload": artifact.payload or {},
            }
            for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        ],
    }


def blueprint_version_metadata(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    return {
        "revision": _revision(blueprint_revision_payload(blueprint, roles, artifacts)),
        "version_scheme": "definition-sha256-16",
        "origin_kind": "seed_blueprint" if blueprint.is_seed else "custom_blueprint",
        "updated_at": blueprint.updated_at,
    }


def enrich_blueprint_payload(
    payload: dict[str, Any],
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    payload["version_metadata"] = blueprint_version_metadata(blueprint, roles, artifacts)
    return payload


def team_definition_metadata(team: TeamDB) -> dict[str, Any]:
    repos = get_repository_registry()
    blueprint = repos.team_blueprint_repo.get_by_id(team.blueprint_id) if team.blueprint_id else None
    snapshot = dict(team.blueprint_snapshot or {})
    snapshot_revision = _revision(snapshot) if snapshot else None
    current_revision = None
    source = "live_team"
    drift_status = "not_blueprint_based"
    if blueprint is not None:
        roles = repos.blueprint_role_repo.get_by_blueprint(blueprint.id)
        artifacts = repos.blueprint_artifact_repo.get_by_blueprint(blueprint.id)
        current_revision = blueprint_version_metadata(blueprint, roles, artifacts)["revision"]
        source = "seed_blueprint_instance" if blueprint.is_seed else "custom_blueprint_instance"
        drift_status = "unknown"
        snapshot_meta = snapshot.get("version_metadata") if isinstance(snapshot.get("version_metadata"), dict) else {}
        snapshot_revision = snapshot_meta.get("revision") or snapshot_revision
        if snapshot_revision:
            drift_status = "in_sync" if snapshot_revision == current_revision else "drifted"
    elif team.role_templates:
        source = "live_customized_team"
    return {
        "origin_kind": source,
        "blueprint_id": team.blueprint_id,
        "snapshot_revision": snapshot_revision,
        "current_blueprint_revision": current_revision,
        "drift_status": drift_status,
        "live_customizations": {
            "role_templates": bool(team.role_templates),
            "members_are_instance_specific": True,
        },
    }


def build_team_blueprint_diff(team_id: str) -> dict[str, Any] | None:
    repos = get_repository_registry()
    team = repos.team_repo.get_by_id(team_id)
    if team is None:
        return None
    metadata = team_definition_metadata(team)
    blueprint = repos.team_blueprint_repo.get_by_id(team.blueprint_id) if team.blueprint_id else None
    snapshot = dict(team.blueprint_snapshot or {})
    current_blueprint = None
    if blueprint is not None:
        roles = repos.blueprint_role_repo.get_by_blueprint(blueprint.id)
        artifacts = repos.blueprint_artifact_repo.get_by_blueprint(blueprint.id)
        current_blueprint = enrich_blueprint_payload(
            {
                **blueprint.model_dump(),
                "roles": [role.model_dump() for role in roles],
                "artifacts": [artifact.model_dump() for artifact in artifacts],
            },
            blueprint,
            roles,
            artifacts,
        )

    def _names(items: list[dict], key: str) -> set[str]:
        return {str(item.get(key) or "").strip() for item in items if str(item.get(key) or "").strip()}

    snapshot_roles = _names(snapshot.get("roles") or [], "name")
    current_roles = _names((current_blueprint or {}).get("roles") or [], "name")
    snapshot_artifacts = _names(snapshot.get("artifacts") or [], "title")
    current_artifacts = _names((current_blueprint or {}).get("artifacts") or [], "title")
    return {
        "team_id": team.id,
        "team_name": team.name,
        "definition_metadata": metadata,
        "snapshot": {
            "blueprint_id": snapshot.get("id") or team.blueprint_id,
            "revision": metadata.get("snapshot_revision"),
            "role_names": sorted(snapshot_roles),
            "artifact_titles": sorted(snapshot_artifacts),
        },
        "current_blueprint": {
            "blueprint_id": (current_blueprint or {}).get("id"),
            "revision": metadata.get("current_blueprint_revision"),
            "role_names": sorted(current_roles),
            "artifact_titles": sorted(current_artifacts),
        },
        "diff": {
            "roles_added": sorted(current_roles - snapshot_roles),
            "roles_removed": sorted(snapshot_roles - current_roles),
            "artifacts_added": sorted(current_artifacts - snapshot_artifacts),
            "artifacts_removed": sorted(snapshot_artifacts - current_artifacts),
            "role_templates_customized": bool(team.role_templates),
        },
    }
