from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from agent.routes import organization_route_support
from agent.routes.organization_route_support import OrganizationRouteError


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class RecordingSession:
    def __init__(self, rows=()) -> None:
        self.statement = None
        self.rows = list(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def exec(self, statement):
        self.statement = statement
        return _Rows(self.rows)


def test_organization_lookup_applies_project_claim_in_sql_and_stays_non_enumerable(
    monkeypatch,
) -> None:
    session = RecordingSession()
    monkeypatch.setattr(
        organization_route_support,
        "get_authenticated_source_control_principal",
        lambda: SimpleNamespace(
            subject_id="operator-a",
            tenant_id="tenant-a",
            project_id="project-claim-a",
            roles=(),
        ),
    )
    monkeypatch.setattr(
        organization_route_support,
        "Session",
        lambda _engine: session,
    )
    app = Flask(__name__)

    with app.test_request_context("/api/organizations/organization-a"):
        with pytest.raises(OrganizationRouteError) as caught:
            organization_route_support.require_organization_scope("organization-a")

    assert caught.value.reason_code == "organization_not_found"
    assert caught.value.status_code == 404
    assert session.statement is not None
    compiled = session.statement.compile()
    assert "project-claim-a" in compiled.params.values()
    assert "organization_memberships.expires_at IS NULL" in str(compiled)


def test_membership_principal_preserves_the_authenticated_project_claim() -> None:
    principal = organization_route_support.OrganizationRequestPrincipal(
        subject_id="operator-a",
        tenant_id="tenant-a",
        project_id="project-claim-a",
        roles=frozenset(),
    )

    assert principal.membership_principal().project_id == "project-claim-a"


def test_unbound_project_claim_fails_closed_when_organization_lookup_is_ambiguous(
    monkeypatch,
) -> None:
    session = RecordingSession(rows=[("organization-a", "membership-a"), ("organization-b", "membership-b")])
    monkeypatch.setattr(
        organization_route_support,
        "get_authenticated_source_control_principal",
        lambda: SimpleNamespace(
            subject_id="operator-a",
            tenant_id="tenant-a",
            project_id=None,
            roles=(),
        ),
    )
    monkeypatch.setattr(
        organization_route_support,
        "Session",
        lambda _engine: session,
    )
    app = Flask(__name__)

    with app.test_request_context("/api/organizations/organization-a"):
        with pytest.raises(OrganizationRouteError) as caught:
            organization_route_support.require_organization_scope("organization-a")

    assert caught.value.reason_code == "organization_not_found"
    assert caught.value.status_code == 404
