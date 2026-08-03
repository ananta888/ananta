from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from flask import Flask, g
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import ApprovalRequestDB
from agent.routes.approvals import (
    _can_decide_approval,
    _can_view_approval,
    _has_project_access,
    _organization_principal,
)
from agent.services.approval_request_service import ApprovalRequestService
from agent.services.project_access_authority import ProjectCapability


class _ProjectAuthority:
    def __init__(self, *allowed: ProjectCapability) -> None:
        self.allowed = set(allowed)
        self.calls: list[dict[str, Any]] = []

    def require(self, **values: Any) -> SimpleNamespace:
        self.calls.append(values)
        if values["capability"] not in self.allowed:
            from agent.services.project_access_authority import (
                ProjectAccessDeniedError,
            )

            raise ProjectAccessDeniedError(
                tenant_id=values["tenant_id"],
                project_id=values["project_id"],
            )
        return SimpleNamespace(**values)


def _app(authority: _ProjectAuthority | None = None) -> Flask:
    app = Flask(__name__)
    if authority is not None:
        app.extensions["project_access_authority"] = authority
    return app


def _approval(**overrides: Any) -> SimpleNamespace:
    values = {
        "tenant_id": "tenant-a",
        "project_id": None,
        "organization_id": None,
        "goal_id": "goal-a",
        "task_id": None,
        "tool_name": "workspace.write",
        "scope": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_visible_goal(monkeypatch) -> None:
    goal = SimpleNamespace(id="goal-a", team_id="team-a")
    repositories = SimpleNamespace(goal_repo=SimpleNamespace(get_by_id=lambda _goal_id: goal))
    goal_service = SimpleNamespace(can_access_goal=lambda *_args: True)
    monkeypatch.setattr(
        "agent.services.repository_registry.get_repository_registry",
        lambda: repositories,
    )
    monkeypatch.setattr(
        "agent.services.goal_service.get_goal_service",
        lambda: goal_service,
    )


def test_organization_principal_preserves_credential_project_claim() -> None:
    app = _app()
    with app.test_request_context("/api/approvals"):
        g.user = {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
        }
        g.is_admin = False

        principal = _organization_principal()

    assert principal.principal_id == "alice"
    assert principal.tenant_id == "tenant-a"
    assert principal.project_id == "project-a"


def test_project_access_rejects_a_filter_outside_credential_claim() -> None:
    authority = _ProjectAuthority(ProjectCapability.READ)
    app = _app(authority)
    with app.test_request_context("/api/approvals?project_id=project-b"):
        g.user = {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
        }
        g.is_admin = False

        allowed = _has_project_access(
            tenant_id="tenant-a",
            project_id="project-b",
            capability=ProjectCapability.READ,
        )

    assert allowed is False
    assert authority.calls == []


def test_visible_legacy_approval_still_requires_an_approver_role(
    monkeypatch,
) -> None:
    _install_visible_goal(monkeypatch)
    app = _app()
    approval = _approval()
    with app.test_request_context("/api/approvals/approval-a/decision"):
        g.user = {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "team_id": "team-a",
            "roles": ["user"],
        }
        g.is_admin = False
        assert _can_view_approval(approval) is True
        assert _can_decide_approval(approval) is False

        g.user["roles"] = ["approval_approver"]
        assert _can_decide_approval(approval) is True


def test_project_manager_can_decide_project_scoped_approval(
    monkeypatch,
) -> None:
    _install_visible_goal(monkeypatch)
    authority = _ProjectAuthority(
        ProjectCapability.READ,
        ProjectCapability.MANAGE,
    )
    app = _app(authority)
    approval = _approval(project_id="project-a")
    with app.test_request_context("/api/approvals/approval-a/decision"):
        g.user = {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "roles": ["user"],
        }
        g.is_admin = False

        assert _can_decide_approval(approval) is True


def test_organization_decision_requires_project_manage_and_mutation_grant(
    monkeypatch,
) -> None:
    authority = _ProjectAuthority(ProjectCapability.READ)
    app = _app(authority)
    approval = _approval(
        project_id="project-a",
        organization_id="organization-a",
        scope={"operation": "track_adopt"},
    )
    mutation_allowed = {"value": False}
    monkeypatch.setattr(
        "agent.services.organization_membership_service.OrganizationMembershipService.can_view",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "agent.services.organization_membership_service.OrganizationMembershipService.can_mutate",
        lambda *_args, **_kwargs: mutation_allowed["value"],
    )

    with app.test_request_context("/api/approvals/approval-a/decision"):
        g.user = {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "roles": ["approval_approver"],
        }
        g.is_admin = False
        assert _can_view_approval(approval) is True
        assert _can_decide_approval(approval) is False

        authority.allowed.add(ProjectCapability.MANAGE)
        assert _can_decide_approval(approval) is False

        mutation_allowed["value"] = True
        assert _can_decide_approval(approval) is True


def test_tenantless_legacy_approval_is_not_inherited_by_tenant_identity(
    monkeypatch,
) -> None:
    _install_visible_goal(monkeypatch)
    app = _app()
    approval = _approval(tenant_id=None)
    with app.test_request_context("/api/approvals/approval-a"):
        g.user = {
            "sub": "tenant-admin",
            "tenant_id": "tenant-a",
            "role": "admin",
        }
        g.is_admin = True

        assert _can_view_approval(approval) is False
        assert _can_decide_approval(approval) is False


def test_tenant_scoped_list_excludes_tenantless_legacy_rows(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "agent.services.approval_request_service._engine",
        lambda: engine,
    )
    with Session(engine) as session:
        session.add(
            ApprovalRequestDB(
                id="approval-scoped",
                tenant_id="tenant-a",
                tool_name="workspace.write",
                arguments_digest="a" * 64,
            )
        )
        session.add(
            ApprovalRequestDB(
                id="approval-tenantless",
                tool_name="workspace.write",
                arguments_digest="b" * 64,
            )
        )
        session.commit()

    rows = ApprovalRequestService().list_requests(
        tenant_id="tenant-a",
        organization_ids=(),
        scope_is_admin=True,
    )

    assert [row.id for row in rows] == ["approval-scoped"]
