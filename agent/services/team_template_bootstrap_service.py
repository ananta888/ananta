"""TeamType + Role + Template bootstrap service.

SRP: seed TeamType, Role, TemplateDB and TeamTypeRoleLink rows from
in-process specs. No blueprint coupling. Idempotent on retry
(integrity-error retry loop). Migrated from team_blueprint_service.py
(WFG-029 split) without behaviour change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import RoleDB, TeamTypeDB, TeamTypeRoleLink, TemplateDB


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


def ensure_default_templates(
    team_type_name: str,
    *,
    team_type_description: str,
    template_specs: list[TemplateBootstrapSpec],
    role_specs: list[RoleLinkSpec],
) -> None:
    for attempt in range(2):
        try:
            _ensure_default_templates_once(
                team_type_name,
                team_type_description=team_type_description,
                template_specs=template_specs,
                role_specs=role_specs,
            )
            return
        except IntegrityError:
            if attempt >= 1:
                raise
            time.sleep(0.05)


def _ensure_default_templates_once(
    team_type_name: str,
    *,
    team_type_description: str,
    template_specs: list[TemplateBootstrapSpec],
    role_specs: list[RoleLinkSpec],
) -> None:
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
                template = TemplateDB(
                    name=spec.name, description=spec.description,
                    prompt_template=spec.prompt_template, is_seed=True,
                )
                session.add(template)
                session.flush()
                templates_by_name[spec.name] = template
                continue
            # Only overwrite seed templates; user-created templates (is_seed=False) are preserved.
            if not getattr(template, "is_seed", True):
                continue
            changed = (template.description != spec.description or template.prompt_template != spec.prompt_template)
            if changed or not getattr(template, "is_seed", True):
                template.description = spec.description
                template.prompt_template = spec.prompt_template
                template.is_seed = True
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
