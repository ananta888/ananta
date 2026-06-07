"""Team materialization from a blueprint (team_type + members + artifacts).

SRP: take a TeamBlueprintDB plus a TeamBlueprintInstantiateRequest,
ensure concrete RoleDB / TeamTypeRoleLink rows exist for each
blueprint role, then create a TeamDB with TeamMemberDB rows and
materialize task-kind artifacts into TaskDB. Owns no blueprint
persistence and no template bootstrap. Migrated from
team_blueprint_service.py (WFG-029 split) without behaviour change.
"""
from __future__ import annotations

import time
import uuid

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
from agent.models import TeamBlueprintInstantiateRequest


def instantiate_blueprint(
    blueprint_id: str,
    data: TeamBlueprintInstantiateRequest,
    *,
    error_factory,
    normalize_team_type_name,
) -> TeamDB | tuple:
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

        from agent.services.team_blueprint_persistence_service import (
            serialize_blueprint_snapshot,
        )
        snapshot = serialize_blueprint_snapshot(blueprint, blueprint_roles, blueprint_artifacts)
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


def _ensure_role_for_blueprint_role_in_session(
    session: Session,
    team_type_id: str | None,
    blueprint_role: BlueprintRoleDB,
) -> RoleDB:
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


def _materialize_blueprint_artifacts_in_session(
    session: Session,
    team: TeamDB,
    blueprint_artifacts: list[BlueprintArtifactDB],
) -> None:
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
