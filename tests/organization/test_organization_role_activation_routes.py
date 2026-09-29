from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask, g

from agent.routes import organization_role_activation as routes
from agent.services.organization_role_activation_read_service import (
    OrganizationRoleActivationReadError,
)


@pytest.fixture()
def app() -> Flask:
    application = Flask(__name__)
    application.register_blueprint(routes.organization_role_activation_bp)
    return application


def _organization():
    return SimpleNamespace(
        organization_id="organization-1",
        tenant_id="tenant-1",
        project_id="project-1",
        definition_revision="d" * 64,
    )


def _install_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    def require_scope(organization_id: str):
        assert organization_id == "organization-1"
        return SimpleNamespace(
            tenant_id="tenant-1",
            project_id="project-1",
            organization=_organization(),
        )

    monkeypatch.setattr(routes, "require_organization_scope", require_scope)


class _ReadService:
    def __init__(self, *, error=None) -> None:
        self.error = error
        self.calls = []

    def read(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {
            "schema": "organization_role_activation_map.v1",
            "organization_id": kwargs["organization"].organization_id,
            "definition_revision": kwargs["organization"].definition_revision,
            "router_owner": "hub",
            "teams": [],
            "edges": [],
        }


def test_blueprint_exposes_only_scoped_read_endpoint(app: Flask) -> None:
    rules = {(rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules()}

    assert rules == {
        ("/static/<path:filename>", ("GET",)),
        (
            "/api/organizations/<organization_id>/role-activation-map",
            ("GET",),
        ),
    }


def test_route_uses_authoritative_scope_and_returns_revision_bound_contract(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    service = _ReadService()
    app.extensions["organization_role_activation_read_service"] = service

    with app.test_request_context("/api/organizations/organization-1/role-activation-map"):
        g.user = {"sub": "operator-1"}
        response, status = routes.get_organization_role_activation_map.__wrapped__("organization-1")

    assert status == 200
    assert response.get_json()["data"] == {
        "schema": "organization_role_activation_map.v1",
        "organization_id": "organization-1",
        "definition_revision": "d" * 64,
        "router_owner": "hub",
        "teams": [],
        "edges": [],
    }
    assert service.calls == [
        {
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization": service.calls[0]["organization"],
        }
    ]
    assert service.calls[0]["organization"].organization_id == "organization-1"


def test_route_rejects_query_fields_before_scope_or_service(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ReadService()
    app.extensions["organization_role_activation_read_service"] = service
    monkeypatch.setattr(
        routes,
        "require_organization_scope",
        lambda _organization_id: pytest.fail("scope must not be read"),
    )

    with app.test_request_context("/api/organizations/organization-1/role-activation-map?include_agent_urls=true"):
        g.user = {"sub": "operator-1"}
        response, status = routes.get_organization_role_activation_map.__wrapped__("organization-1")

    assert status == 400
    assert response.get_json()["message"] == "organization_query_fields_invalid"
    assert service.calls == []


def test_route_maps_projection_integrity_failure_without_internal_details(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    service = _ReadService(
        error=OrganizationRoleActivationReadError(
            "organization_role_activation_workflow_definition_missing",
            details={"workflow_ref": "missing@1"},
        )
    )
    app.extensions["organization_role_activation_read_service"] = service

    with app.test_request_context("/api/organizations/organization-1/role-activation-map"):
        g.user = {"sub": "operator-1"}
        response, status = routes.get_organization_role_activation_map.__wrapped__("organization-1")

    assert status == 409
    assert response.get_json()["message"] == ("organization_role_activation_workflow_definition_missing")
    assert response.get_json()["data"]["workflow_ref"] == "missing@1"
