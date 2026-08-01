from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import TeamDB
from agent.models.project_models import (
    ProjectCreateCommand,
    ProjectMembershipUpsertCommand,
)
from agent.services.project_access_authority import (
    ProjectAccessDeniedError,
    ProjectArchivedError,
    ProjectCapability,
    SqlProjectAccessAuthority,
)
from agent.services.project_lifecycle_service import ProjectLifecycleService


@pytest.fixture()
def project_runtime():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def sessions() -> Session:
        return Session(engine)

    return (
        ProjectLifecycleService(session_factory=sessions),
        SqlProjectAccessAuthority(session_factory=sessions),
        sessions,
    )


def test_create_is_atomic_and_membership_controls_archive(project_runtime):
    service, authority, sessions = project_runtime
    created = service.create_project(
        ProjectCreateCommand(
            tenant_id="tenant-a",
            name="Project Alpha",
            owner_subject_id="owner-a",
        )
    )

    assert created.id == created.team_id
    assert created.status == "active"
    with sessions() as session:
        team = session.exec(
            select(TeamDB).where(TeamDB.id == created.team_id)
        ).first()
    assert team is not None
    assert team.name == "Project Alpha"

    owner_scope = authority.require(
        tenant_id="tenant-a",
        project_id=created.id,
        subject_id="owner-a",
        capability=ProjectCapability.MANAGE_MEMBERS,
    )
    member = service.upsert_member(
        owner_scope,
        ProjectMembershipUpsertCommand(
            subject_id="viewer-a",
            role="viewer",
        ),
    )
    assert member.role == "viewer"

    authority.require(
        tenant_id="tenant-a",
        project_id=created.id,
        subject_id="viewer-a",
        capability=ProjectCapability.READ,
    )
    with pytest.raises(ProjectAccessDeniedError):
        authority.require(
            tenant_id="tenant-a",
            project_id=created.id,
            subject_id="viewer-a",
            capability=ProjectCapability.WRITE,
        )

    archived = service.archive_project(
        authority.require(
            tenant_id="tenant-a",
            project_id=created.id,
            subject_id="owner-a",
            capability=ProjectCapability.ARCHIVE,
        )
    )
    assert archived.status == "archived"
    assert archived.is_active is False
    with pytest.raises(ProjectArchivedError):
        authority.require(
            tenant_id="tenant-a",
            project_id=created.id,
            subject_id="owner-a",
            capability=ProjectCapability.READ,
        )


def test_project_scope_is_tenant_bound_and_hidden(project_runtime):
    service, authority, _sessions = project_runtime
    created = service.create_project(
        ProjectCreateCommand(
            tenant_id="tenant-a",
            name="Tenant A",
            owner_subject_id="owner-a",
        )
    )

    from agent.services.project_access_authority import ProjectNotFoundError

    with pytest.raises(ProjectNotFoundError):
        authority.require(
            tenant_id="tenant-b",
            project_id=created.id,
            subject_id="owner-a",
            capability=ProjectCapability.READ,
            tenant_admin=True,
        )
