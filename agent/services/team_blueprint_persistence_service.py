"""Blueprint JSON load + save + children persistence.

SRP: create / update TeamBlueprintDB rows, persist their BlueprintRoleDB
and BlueprintArtifactDB children idempotently, and serialize snapshots.
Owns no template/role bootstrap and no team instantiation.
Migrated from team_blueprint_service.py (WFG-029 split) without
behaviour change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    BlueprintArtifactDB,
    BlueprintRoleDB,
    TeamBlueprintDB,
)
from agent.models import (
    BlueprintArtifactDefinition,
    BlueprintRoleDefinition,
)
from agent.services.team_definition_version_service import enrich_blueprint_payload


@dataclass(frozen=True)
class PersistBlueprintChildrenResult:
    roles: list[BlueprintRoleDB]
    artifacts: list[BlueprintArtifactDB]
    changed: bool
    changes: dict


@dataclass(frozen=True)
class BlueprintSaveResult:
    blueprint: TeamBlueprintDB
    roles: list[BlueprintRoleDB]
    artifacts: list[BlueprintArtifactDB]
    changes: dict


def persist_blueprint_children(
    blueprint_id: str,
    role_definitions: list[BlueprintRoleDefinition] | None,
    artifact_definitions: list[BlueprintArtifactDefinition] | None,
) -> tuple[list[BlueprintRoleDB], list[BlueprintArtifactDB]]:
    with Session(engine) as session:
        result = persist_blueprint_children_in_session(session, blueprint_id, role_definitions, artifact_definitions)
        blueprint = session.get(TeamBlueprintDB, blueprint_id)
        if blueprint is not None and result.changed:
            blueprint.updated_at = time.time()
            session.add(blueprint)
        session.commit()
        persisted_roles = session.exec(
            select(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint_id).order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
        ).all()
        persisted_artifacts = session.exec(
            select(BlueprintArtifactDB)
            .where(BlueprintArtifactDB.blueprint_id == blueprint_id)
            .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
        ).all()
        return persisted_roles, persisted_artifacts


def persist_blueprint_children_in_session(
    session: Session,
    blueprint_id: str,
    role_definitions: list[BlueprintRoleDefinition] | None,
    artifact_definitions: list[BlueprintArtifactDefinition] | None,
) -> PersistBlueprintChildrenResult:
    existing_roles = session.exec(
        select(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint_id).order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
    ).all()
    existing_artifacts = session.exec(
        select(BlueprintArtifactDB)
        .where(BlueprintArtifactDB.blueprint_id == blueprint_id)
        .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
    ).all()

    changed = False
    changes = {
        "roles": {"created": [], "updated": [], "deleted": []},
        "artifacts": {"created": [], "updated": [], "deleted": []},
    }
    persisted_roles = existing_roles
    persisted_artifacts = existing_artifacts

    if role_definitions is not None:
        role_map = {role.name.strip().lower(): role for role in existing_roles}
        persisted_roles = []
        retained_role_ids: set[str] = set()
        for role_def in role_definitions:
            key = role_def.name.strip().lower()
            role = role_map.get(key)
            if role is None:
                role = BlueprintRoleDB(blueprint_id=blueprint_id)
                changed = True
                changes["roles"]["created"].append({"name": role_def.name.strip()})
            role_name = role_def.name.strip()
            role_config = dict(role_def.config or {})
            updated_fields = []
            if role.name != role_name:
                updated_fields.append("name")
            if role.description != role_def.description:
                updated_fields.append("description")
            if role.template_id != role_def.template_id:
                updated_fields.append("template_id")
            if role.sort_order != role_def.sort_order:
                updated_fields.append("sort_order")
            if role.is_required != role_def.is_required:
                updated_fields.append("is_required")
            if dict(role.config or {}) != role_config:
                updated_fields.append("config")
            if (
                role.name != role_name
                or role.description != role_def.description
                or role.template_id != role_def.template_id
                or role.sort_order != role_def.sort_order
                or role.is_required != role_def.is_required
                or dict(role.config or {}) != role_config
            ):
                changed = True
                if updated_fields and role.id:
                    changes["roles"]["updated"].append({"name": role_name, "fields": updated_fields})
            role.name = role_name
            role.description = role_def.description
            role.template_id = role_def.template_id
            role.sort_order = role_def.sort_order
            role.is_required = role_def.is_required
            role.config = role_config
            session.add(role)
            session.flush()
            retained_role_ids.add(role.id)
            persisted_roles.append(role)
        for existing_role in existing_roles:
            if existing_role.id not in retained_role_ids:
                changed = True
                changes["roles"]["deleted"].append({"name": existing_role.name})
                session.delete(existing_role)

    if artifact_definitions is not None:
        artifact_map = {artifact.title.strip().lower(): artifact for artifact in existing_artifacts}
        persisted_artifacts = []
        retained_artifact_ids: set[str] = set()
        for artifact_def in artifact_definitions:
            key = artifact_def.title.strip().lower()
            artifact = artifact_map.get(key)
            if artifact is None:
                artifact = BlueprintArtifactDB(blueprint_id=blueprint_id)
                changed = True
                changes["artifacts"]["created"].append({"title": artifact_def.title.strip(), "kind": artifact_def.kind.strip()})
            artifact_title = artifact_def.title.strip()
            artifact_kind = artifact_def.kind.strip()
            artifact_payload = dict(artifact_def.payload or {})
            updated_fields = []
            if artifact.kind != artifact_kind:
                updated_fields.append("kind")
            if artifact.title != artifact_title:
                updated_fields.append("title")
            if artifact.description != artifact_def.description:
                updated_fields.append("description")
            if artifact.sort_order != artifact_def.sort_order:
                updated_fields.append("sort_order")
            if dict(artifact.payload or {}) != artifact_payload:
                updated_fields.append("payload")
            if (
                artifact.kind != artifact_kind
                or artifact.title != artifact_title
                or artifact.description != artifact_def.description
                or artifact.sort_order != artifact_def.sort_order
                or dict(artifact.payload or {}) != artifact_payload
            ):
                changed = True
                if updated_fields and artifact.id:
                    changes["artifacts"]["updated"].append({"title": artifact_title, "fields": updated_fields})
            artifact.kind = artifact_kind
            artifact.title = artifact_title
            artifact.description = artifact_def.description
            artifact.sort_order = artifact_def.sort_order
            artifact.payload = artifact_payload
            session.add(artifact)
            session.flush()
            retained_artifact_ids.add(artifact.id)
            persisted_artifacts.append(artifact)
        for existing_artifact in existing_artifacts:
            if existing_artifact.id not in retained_artifact_ids:
                changed = True
                changes["artifacts"]["deleted"].append({"title": existing_artifact.title, "kind": existing_artifact.kind})
                session.delete(existing_artifact)

    session.flush()
    persisted_roles = session.exec(
        select(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint_id).order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
    ).all()
    persisted_artifacts = session.exec(
        select(BlueprintArtifactDB)
        .where(BlueprintArtifactDB.blueprint_id == blueprint_id)
        .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
    ).all()
    return PersistBlueprintChildrenResult(roles=persisted_roles, artifacts=persisted_artifacts, changed=changed, changes=changes)


def save_blueprint(
    *,
    blueprint_id: str | None,
    name: str,
    description: str | None,
    base_team_type_name: str | None,
    roles: list[BlueprintRoleDefinition] | None,
    artifacts: list[BlueprintArtifactDefinition] | None,
    is_seed: bool | None = None,
) -> BlueprintSaveResult:
    with Session(engine) as session:
        blueprint = session.get(TeamBlueprintDB, blueprint_id) if blueprint_id else None
        blueprint_field_changes: list[str] = []
        if blueprint is None:
            blueprint = TeamBlueprintDB(
                name=name,
                description=description,
                base_team_type_name=base_team_type_name,
                is_seed=bool(is_seed),
            )
            session.add(blueprint)
            session.flush()
            blueprint_field_changes = ["name", "description", "base_team_type_name", "is_seed"]
        else:
            if blueprint.name != name:
                blueprint_field_changes.append("name")
            if blueprint.description != description:
                blueprint_field_changes.append("description")
            if blueprint.base_team_type_name != base_team_type_name:
                blueprint_field_changes.append("base_team_type_name")
            if is_seed is not None and blueprint.is_seed != is_seed:
                blueprint_field_changes.append("is_seed")
            blueprint.name = name
            blueprint.description = description
            blueprint.base_team_type_name = base_team_type_name
            if is_seed is not None:
                blueprint.is_seed = is_seed
            if blueprint_field_changes:
                blueprint.updated_at = time.time()
            session.add(blueprint)
            session.flush()

        result = persist_blueprint_children_in_session(session, blueprint.id, roles, artifacts)
        if blueprint_field_changes or result.changed:
            blueprint.updated_at = time.time()
            session.add(blueprint)
        session.commit()
        session.refresh(blueprint)
        persisted_roles = session.exec(
            select(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint.id).order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
        ).all()
        persisted_artifacts = session.exec(
            select(BlueprintArtifactDB)
            .where(BlueprintArtifactDB.blueprint_id == blueprint.id)
            .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
        ).all()
        return BlueprintSaveResult(
            blueprint=blueprint,
            roles=persisted_roles,
            artifacts=persisted_artifacts,
            changes={
                "blueprint_fields": blueprint_field_changes,
                "roles": result.changes["roles"],
                "artifacts": result.changes["artifacts"],
                "changed": bool(blueprint_field_changes or result.changed),
            },
        )


def serialize_blueprint_snapshot(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict:
    """Render a snapshot dict for a blueprint + its children."""
    payload = blueprint.model_dump()
    payload["roles"] = [role.model_dump() for role in roles]
    payload["artifacts"] = [artifact.model_dump() for artifact in artifacts]
    # WFG-033: include the persisted workflow block in the
    # snapshot. The team stores the snapshot at instantiation
    # time, so a later change to the catalog workflow DOES
    # NOT propagate automatically. The reconciliation report
    # flags drifted snapshots via the ``definition_metadata``
    # field. The e2e gate-decision flow (WFG-025) asserts
    # on the snapshot's ``workflow`` field directly.
    payload["workflow"] = _workflow_from_db(blueprint.id)
    return enrich_blueprint_payload(payload, blueprint, roles, artifacts)


def _workflow_from_db(blueprint_id: str) -> dict | None:
    """Read the workflow block from blueprint_workflow_steps.

    The function is safe to call outside of a session (it
    opens its own) and returns ``None`` when the blueprint
    has no workflow block (the legacy / artifact-based
    path).
    """
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models import BlueprintWorkflowStepDB
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(BlueprintWorkflowStepDB)
                .where(BlueprintWorkflowStepDB.blueprint_id == blueprint_id)
                .order_by(BlueprintWorkflowStepDB.sort_order.asc())
            ).all()
        )
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
                "depends_on": list(r.depends_on or []),
                "gate": bool(r.gate),
                "checks": dict(r.checks or {}),
                "failure_policy": r.failure_policy,
                "required_capabilities": list(r.required_capabilities or []),
                "sort_order": int(r.sort_order),
                "pattern_hints": dict(r.pattern_hints) if r.pattern_hints else None,
            }
            for r in rows
        ],
    }
