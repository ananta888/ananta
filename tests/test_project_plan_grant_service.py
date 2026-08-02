from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import OrganizationAdminGrantDB
from agent.services.project_plan_grant_service import (
    ProjectPlanGrantError,
    ProjectPlanGrantService,
)


def _service(*, now: list[float]) -> tuple[ProjectPlanGrantService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[OrganizationAdminGrantDB.__table__],
    )
    return (
        ProjectPlanGrantService(
            session_factory=lambda: Session(engine),
            clock=lambda: now[0],
        ),
        engine,
    )


def _issue(
    service: ProjectPlanGrantService,
    *,
    idempotency_key: str,
) -> dict:
    return service.issue(
        tenant_id="tenant-a",
        project_id="project-a",
        principal_id="principal-a",
        plan_digest="a" * 64,
        policy_hash="b" * 64,
        grant_kind="instantiate",
        granted_by="admin-a",
        idempotency_key=idempotency_key,
        ttl_seconds=60,
    )


def test_consumed_plan_grant_can_be_reissued_with_a_fresh_key() -> None:
    now = [1000.0]
    service, engine = _service(now=now)
    first = _issue(service, idempotency_key="request-key-0001")
    with Session(engine) as session, session.begin():
        service.consume_in_session(
            session,
            grant_id=first["grant_id"],
            tenant_id="tenant-a",
            project_id="project-a",
            principal_id="principal-a",
            plan_digest="a" * 64,
            policy_hash="b" * 64,
            grant_kind="instantiate",
        )

    second = _issue(service, idempotency_key="request-key-0002")

    assert second["grant_id"] != first["grant_id"]
    assert second["replayed"] is False
    with pytest.raises(ProjectPlanGrantError, match="project_plan_grant_already_consumed"):
        _issue(service, idempotency_key="request-key-0001")


def test_expired_plan_grant_can_be_reissued_without_changing_plan_digest() -> None:
    now = [1000.0]
    service, _engine = _service(now=now)
    first = _issue(service, idempotency_key="request-key-0001")
    now[0] = 1061.0

    second = _issue(service, idempotency_key="request-key-0002")

    assert second["grant_id"] != first["grant_id"]
    with pytest.raises(ProjectPlanGrantError, match="project_plan_grant_expired"):
        _issue(service, idempotency_key="request-key-0001")
