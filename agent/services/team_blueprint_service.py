from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    BlueprintArtifactDB,
    BlueprintRoleDB,
    RoleDB,
    TaskDB,
    TeamBlueprintDB,
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    TemplateDB,
)
from agent.models import (
    BlueprintArtifactDefinition,
    BlueprintRoleDefinition,
    TeamBlueprintCreateRequest,
    TeamBlueprintInstantiateRequest,
    TeamBlueprintUpdateRequest,
)


@dataclass(frozen=True)
class TemplateBootstrapSpec:
    name: str
    description: str
    prompt_template: str


@dataclass(frozen=True)
class RoleLinkSpec:
    role_name: str
    role_description: str
    template_name: str


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


def ensure_default_templates(team_type_name: str, *, team_type_description: str, template_specs: list[TemplateBootstrapSpec], role_specs: list[RoleLinkSpec]) -> None:
    with Session(engine) as session:
        team_type = session.exec(select(TeamTypeDB).where(TeamTypeDB.name == team_type_name)).first()
        if team_type is None:
            team_type = TeamTypeDB(name=team_type_name, description=team_type_description)
            session.add(team_type)
            session.flush()
        elif team_type.description != team_type_description:
            team_type.description = team_type_description
            session.add(team_type)

        templates_by_name = {template.name: template for template in session.exec(select(TemplateDB)).all()}
        for spec in template_specs:
            template = templates_by_name.get(spec.name)
            if template is None:
                template = TemplateDB(name=spec.name, description=spec.description, prompt_template=spec.prompt_template)
                session.add(template)
                session.flush()
                templates_by_name[spec.name] = template
                continue
            if template.description != spec.description or template.prompt_template != spec.prompt_template:
                template.description = spec.description
                template.prompt_template = spec.prompt_template
                session.add(template)

        roles_by_name = {role.name: role for role in session.exec(select(RoleDB)).all()}
        for spec in role_specs:
            template = templates_by_name[spec.template_name]
            role = roles_by_name.get(spec.role_name)
            if role is None:
                role = RoleDB(name=spec.role_name, description=spec.role_description, default_template_id=template.id)
                session.add(role)
                session.flush()
                roles_by_name[spec.role_name] = role
            else:
                changed = False
                if role.description != spec.role_description:
                    role.description = spec.role_description
                    changed = True
                if role.default_template_id != template.id:
                    role.default_template_id = template.id
                    changed = True
                if changed:
                    session.add(role)

            link = session.exec(
                select(TeamTypeRoleLink).where(
                    TeamTypeRoleLink.team_type_id == team_type.id,
                    TeamTypeRoleLink.role_id == role.id,
                )
            ).first()
            if link is None:
                link = TeamTypeRoleLink(team_type_id=team_type.id, role_id=role.id, template_id=template.id)
                session.add(link)
            elif link.template_id != template.id:
                link.template_id = template.id
                session.add(link)

        session.commit()


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


def reconcile_seed_blueprints(
    seed_blueprints: dict,
    *,
    normalize_team_type_name,
    with_role_profile_defaults,
    ensure_default_templates_callback,
) -> list[dict]:
    reconcile_reports: list[dict] = []
    for blueprint_name, blueprint_definition in seed_blueprints.items():
        base_team_type_name = normalize_team_type_name(str(blueprint_definition.get("base_team_type_name") or blueprint_name))
        ensure_default_templates_callback(base_team_type_name)
        with Session(engine) as session:
            templates_by_name = {template.name: template for template in session.exec(select(TemplateDB)).all()}
            blueprint = session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == blueprint_name)).first()
            blueprint_field_changes: list[str] = []
            if blueprint is None:
                blueprint = TeamBlueprintDB(
                    name=blueprint_name,
                    description=blueprint_definition["description"],
                    base_team_type_name=base_team_type_name or None,
                    is_seed=True,
                )
                session.add(blueprint)
                session.flush()
                blueprint_field_changes = ["name", "description", "base_team_type_name", "is_seed"]
            else:
                if (
                    blueprint.description != blueprint_definition["description"]
                    or blueprint.base_team_type_name != (base_team_type_name or None)
                    or blueprint.is_seed is not True
                ):
                    if blueprint.description != blueprint_definition["description"]:
                        blueprint_field_changes.append("description")
                    if blueprint.base_team_type_name != (base_team_type_name or None):
                        blueprint_field_changes.append("base_team_type_name")
                    if blueprint.is_seed is not True:
                        blueprint_field_changes.append("is_seed")
                    blueprint.description = blueprint_definition["description"]
                    blueprint.base_team_type_name = base_team_type_name or None
                    blueprint.is_seed = True
                    blueprint.updated_at = time.time()
                    session.add(blueprint)
                    session.flush()

            role_definitions = [
                BlueprintRoleDefinition(
                    name=role_definition["name"],
                    description=role_definition["description"],
                    template_id=(templates_by_name.get(role_definition["template_name"]).id if templates_by_name.get(role_definition["template_name"]) else None),
                    sort_order=role_definition["sort_order"],
                    is_required=role_definition["is_required"],
                    config=with_role_profile_defaults(base_team_type_name, role_definition["name"], role_definition["config"]),
                )
                for role_definition in blueprint_definition["roles"]
            ]
            artifact_definitions = [
                BlueprintArtifactDefinition(
                    kind=artifact_definition["kind"],
                    title=artifact_definition["title"],
                    description=artifact_definition["description"],
                    sort_order=artifact_definition["sort_order"],
                    payload=artifact_definition["payload"],
                )
                for artifact_definition in blueprint_definition["artifacts"]
            ]
            result = persist_blueprint_children_in_session(session, blueprint.id, role_definitions, artifact_definitions)
            if blueprint_field_changes or result.changed:
                blueprint.updated_at = time.time()
                session.add(blueprint)
            session.commit()
            if blueprint_field_changes or result.changed:
                reconcile_reports.append(
                    {
                        "blueprint_id": blueprint.id,
                        "name": blueprint.name,
                        "changes": {
                            "blueprint_fields": blueprint_field_changes,
                            "roles": result.changes["roles"],
                            "artifacts": result.changes["artifacts"],
                            "changed": True,
                        },
                    }
                )
    return reconcile_reports


def instantiate_blueprint(blueprint_id: str, data: TeamBlueprintInstantiateRequest, *, error_factory, normalize_team_type_name) -> TeamDB | tuple:
    with Session(engine) as session:
        blueprint = session.get(TeamBlueprintDB, blueprint_id)
        if blueprint is None:
            return error_factory("not_found", 404)

        blueprint_roles = session.exec(
            select(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint.id).order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
        ).all()
        blueprint_artifacts = session.exec(
            select(BlueprintArtifactDB)
            .where(BlueprintArtifactDB.blueprint_id == blueprint.id)
            .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
        ).all()

        team_type = None
        normalized_type_name = normalize_team_type_name(blueprint.base_team_type_name or "")
        if normalized_type_name:
            team_type = session.exec(select(TeamTypeDB).where(TeamTypeDB.name == normalized_type_name)).first()
            if team_type is None:
                return error_factory("team_type_not_found", 404, team_type_name=normalized_type_name)

        blueprint_role_map = {role.id: role for role in blueprint_roles}
        role_bindings: dict[str, str] = {}
        for blueprint_role in blueprint_roles:
            role = _ensure_role_for_blueprint_role_in_session(session, team_type.id if team_type else None, blueprint_role)
            role_bindings[blueprint_role.id] = role.id

        members_payload = []
        for member in data.members:
            role_id = member.role_id
            if member.blueprint_role_id:
                blueprint_role = blueprint_role_map.get(member.blueprint_role_id)
                if not blueprint_role:
                    return error_factory("blueprint_role_not_found", 404, blueprint_role_id=member.blueprint_role_id)
                role_id = role_bindings.get(member.blueprint_role_id)
            if not role_id:
                return error_factory("role_id_required", 400)
            role = session.get(RoleDB, role_id)
            if role is None:
                return error_factory("role_not_found", 404, role_id=role_id)
            if member.custom_template_id and session.get(TemplateDB, member.custom_template_id) is None:
                return error_factory("template_not_found", 404, template_id=member.custom_template_id)
            members_payload.append((member, role_id))

        snapshot = _serialize_blueprint_snapshot(blueprint, blueprint_roles, blueprint_artifacts)
        team = TeamDB(
            name=data.name,
            description=data.description or blueprint.description,
            team_type_id=team_type.id if team_type else None,
            blueprint_id=blueprint.id,
            is_active=data.activate,
            role_templates={role_bindings[role.id]: role.template_id for role in blueprint_roles if role_bindings.get(role.id)},
            blueprint_snapshot=snapshot,
        )
        if data.activate:
            for other in session.exec(select(TeamDB)).all():
                other.is_active = False
                session.add(other)
        session.add(team)
        session.flush()

        for member, role_id in members_payload:
            session.add(
                TeamMemberDB(
                    team_id=team.id,
                    agent_url=member.agent_url,
                    role_id=role_id,
                    blueprint_role_id=member.blueprint_role_id,
                    custom_template_id=member.custom_template_id,
                )
            )

        _materialize_blueprint_artifacts_in_session(session, team, blueprint_artifacts)
        session.commit()
        session.refresh(team)
        return team


def _ensure_role_for_blueprint_role_in_session(session: Session, team_type_id: str | None, blueprint_role: BlueprintRoleDB) -> RoleDB:
    role = session.exec(select(RoleDB).where(RoleDB.name == blueprint_role.name)).first()
    if role is None:
        role = RoleDB(
            name=blueprint_role.name,
            description=blueprint_role.description,
            default_template_id=blueprint_role.template_id,
        )
        session.add(role)
        session.flush()
    else:
        changed = False
        if blueprint_role.description and role.description != blueprint_role.description:
            role.description = blueprint_role.description
            changed = True
        if blueprint_role.template_id and role.default_template_id != blueprint_role.template_id:
            role.default_template_id = blueprint_role.template_id
            changed = True
        if changed:
            session.add(role)

    if team_type_id:
        link = session.exec(
            select(TeamTypeRoleLink).where(
                TeamTypeRoleLink.team_type_id == team_type_id,
                TeamTypeRoleLink.role_id == role.id,
            )
        ).first()
        target_template_id = blueprint_role.template_id or role.default_template_id
        if link is None:
            session.add(TeamTypeRoleLink(team_type_id=team_type_id, role_id=role.id, template_id=target_template_id))
        elif link.template_id != target_template_id:
            link.template_id = target_template_id
            session.add(link)
    session.flush()
    return role


def _materialize_blueprint_artifacts_in_session(session: Session, team: TeamDB, blueprint_artifacts: list[BlueprintArtifactDB]) -> None:
    for artifact in blueprint_artifacts:
        if artifact.kind != "task":
            continue
        payload = artifact.payload or {}
        session.add(
            TaskDB(
                id=str(uuid.uuid4()),
                title=f"{team.name}: {artifact.title}",
                description=artifact.description,
                status=payload.get("status", "todo"),
                priority=payload.get("priority", "Medium"),
                created_at=time.time(),
                updated_at=time.time(),
                team_id=team.id,
            )
        )
    session.flush()


def _serialize_blueprint_snapshot(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict:
    payload = blueprint.model_dump()
    payload["roles"] = [role.model_dump() for role in roles]
    payload["artifacts"] = [artifact.model_dump() for artifact in artifacts]
    return payload
