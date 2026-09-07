"""Minimal deterministic organization graph in the test-owned SQL database."""

from sqlmodel import Session

from agent.db_models import (
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    ProjectDB,
    TaskDB,
    TeamDB,
)


def seed_parent(engine, *, tenant="tenant", project="project", create_project=True):
    scope = {"tenant_id": tenant, "project_id": project, "organization_id": "meet-test-org"}
    with Session(engine) as session:
        if create_project:
            session.add(
                ProjectDB(tenant_id=tenant, project_id=project, name="Synthetic Meet", created_by_subject_id="owner")
            )
            session.commit()
        session.add(
            OrganizationInstanceDB(
                **scope,
                name="Synthetic organization",
                definition_key="synthetic",
                definition_version=1,
                definition_revision="a" * 64,
                lifecycle="active",
                effective_limit_profile_ref="synthetic",
                effective_limit_profile_revision=1,
                effective_limit_profile_hash="b" * 64,
                composition_mode="custom",
                plan_digest="c" * 64,
                idempotency_key="synthetic-meet-parent",
            )
        )
        session.commit()
        session.add(
            OrganizationUnitDB(
                **scope,
                id="meet-test-unit",
                unit_key="synthetic",
                name="Synthetic unit",
                unit_kind="team",
                lifecycle="active",
            )
        )
        session.add(TeamDB(id="meet-test-team", name="Synthetic team", is_active=True))
        session.commit()
        session.add(
            OrganizationTeamLinkDB(**scope, unit_id="meet-test-unit", team_id="meet-test-team", lifecycle="active")
        )
        session.add(
            OrganizationRoleSlotDB(
                **scope,
                id="meet-test-slot",
                unit_id="meet-test-unit",
                slot_key="synthetic",
                role_template_key="synthetic",
                role_template_version=1,
            )
        )
        session.commit()
        session.add(
            TaskDB(
                **scope,
                id="meet-test-parent",
                unit_id="meet-test-unit",
                team_id="meet-test-team",
                role_slot_id="meet-test-slot",
                status="in_progress",
                title="Synthetic parent",
            )
        )
        session.commit()
