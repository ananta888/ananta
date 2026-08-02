from __future__ import annotations

import time

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from agent.db_models import OrganizationAdminGrantDB, OrganizationMembershipDB
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)


@pytest.fixture()
def authorization() -> tuple[OrganizationMembershipService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    OrganizationMembershipDB.__table__.create(engine)
    OrganizationAdminGrantDB.__table__.create(engine)
    now = time.time()
    with Session(engine) as session:
        session.add_all(
            [
                OrganizationMembershipDB(
                    membership_id="membership-viewer",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="viewer-a",
                    membership_kind="viewer",
                ),
                OrganizationMembershipDB(
                    membership_id="membership-admin",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="admin-a",
                    membership_kind="organization_admin",
                ),
                OrganizationMembershipDB(
                    membership_id="membership-service",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="service-a",
                    membership_kind="organization_admin",
                ),
                OrganizationMembershipDB(
                    membership_id="membership-other-project",
                    tenant_id="tenant-a",
                    project_id="project-b",
                    organization_id="organization-b",
                    principal_id="admin-a",
                    membership_kind="organization_admin",
                ),
                OrganizationMembershipDB(
                    membership_id="membership-expired",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-expired",
                    principal_id="expired-a",
                    membership_kind="organization_admin",
                    expires_at=now - 1,
                ),
            ]
        )
        session.add_all(
            [
                OrganizationAdminGrantDB(
                    grant_id="grant-adopt",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="admin-a",
                    grant_kind="approval:track_adopt",
                    policy_hash="policy-a",
                    granted_by="security-test",
                ),
                OrganizationAdminGrantDB(
                    grant_id="grant-service",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="service-a",
                    grant_kind="approval:proposal_amend",
                    policy_hash="policy-a",
                    granted_by="security-test",
                ),
                OrganizationAdminGrantDB(
                    grant_id="grant-expired",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    organization_id="organization-a",
                    principal_id="admin-a",
                    grant_kind="approval:category_promote",
                    policy_hash="policy-a",
                    granted_by="security-test",
                    expires_at=now - 1,
                ),
            ]
        )
        session.commit()
    return OrganizationMembershipService(session_factory=lambda: Session(engine)), engine


@pytest.mark.parametrize(
    ("principal", "tenant_id", "project_id", "organization_id", "allowed"),
    [
        (OrganizationAccessPrincipal("viewer-a", "tenant-a"), "tenant-a", "project-a", "organization-a", True),
        (OrganizationAccessPrincipal("viewer-a", "tenant-b"), "tenant-a", "project-a", "organization-a", False),
        (OrganizationAccessPrincipal("viewer-a", "tenant-a"), "tenant-a", "project-b", "organization-a", False),
        (OrganizationAccessPrincipal("viewer-a", "tenant-a"), "tenant-a", "project-a", "organization-b", False),
        (OrganizationAccessPrincipal("unknown-a", "tenant-a"), "tenant-a", "project-a", "organization-a", False),
        (OrganizationAccessPrincipal("", "tenant-a"), "tenant-a", "project-a", "organization-a", False),
        (OrganizationAccessPrincipal("expired-a", "tenant-a"), "tenant-a", "project-a", "organization-expired", False),
    ],
)
def test_view_matrix_is_exact_and_fail_closed(
    authorization: tuple[OrganizationMembershipService, object],
    principal: OrganizationAccessPrincipal,
    tenant_id: str,
    project_id: str,
    organization_id: str,
    allowed: bool,
) -> None:
    service, _engine = authorization

    assert (
        service.can_view(
            principal=principal,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        is allowed
    )


def test_mutation_requires_admin_membership_and_the_exact_active_grant(
    authorization: tuple[OrganizationMembershipService, object],
) -> None:
    service, _engine = authorization
    viewer = OrganizationAccessPrincipal("viewer-a", "tenant-a")
    admin = OrganizationAccessPrincipal("admin-a", "tenant-a")

    assert (
        service.can_mutate(
            principal=viewer,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            grant_kind="approval:track_adopt",
        )
        is False
    )
    assert (
        service.can_mutate(
            principal=admin,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            grant_kind="approval:track_adopt",
        )
        is True
    )
    assert (
        service.can_mutate(
            principal=admin,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            grant_kind="approval:category_promote",
        )
        is False
    )
    assert (
        service.can_mutate(
            principal=admin,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            grant_kind="approval:proposal_amend",
        )
        is False
    )


def test_service_credential_gets_no_implicit_global_authority(
    authorization: tuple[OrganizationMembershipService, object],
) -> None:
    service, _engine = authorization
    explicitly_bound = OrganizationAccessPrincipal("service-a", "tenant-a", credential_type="service")
    unbound = OrganizationAccessPrincipal("global-service", "tenant-a", credential_type="service")

    assert (
        service.can_mutate(
            principal=explicitly_bound,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            grant_kind="approval:proposal_amend",
        )
        is True
    )
    assert (
        service.can_view(
            principal=unbound,
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
        )
        is False
    )


def test_authorized_organization_ids_do_not_cross_project_or_include_expired_rows(
    authorization: tuple[OrganizationMembershipService, object],
) -> None:
    service, _engine = authorization
    admin = OrganizationAccessPrincipal("admin-a", "tenant-a")
    expired = OrganizationAccessPrincipal("expired-a", "tenant-a")

    assert service.authorized_organization_ids(principal=admin) == frozenset({"organization-a", "organization-b"})
    assert service.authorized_organization_ids(
        principal=admin,
        project_id="project-a",
    ) == frozenset({"organization-a"})
    assert service.authorized_organization_ids(principal=expired) == frozenset()
