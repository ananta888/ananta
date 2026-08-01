from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

import agent.routes.source_control_v1 as routes
from agent.routes.source_control_v1 import (
    create_source_control_v1_blueprint,
)
from tests.project_access_fakes import AllowProjectAccess


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "owner-example"
    tenant_id: str = "tenant-example"
    project_id: str = "project-example"
    roles: frozenset[str] = frozenset({"project_owner"})


class _Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.raise_detail = False

    def binding(self, *, resource_kind, resource_id):
        if resource_id == "missing":
            return None
        return {
            "tenant_id": "tenant-example",
            "project_id": "project-example",
            "owner_id": "owner-example",
        }

    def list_connections(self, **kwargs):
        return {"items": [], "next_cursor": None}

    def get_connection(self, **kwargs):
        if self.raise_detail:
            raise RuntimeError("do-not-leak-this-detail")
        return (
            {
                "connection": {"connection_id": kwargs["connection_id"]},
                "etag": "a" * 64,
            },
            "a" * 64,
        )

    def validate_connection(self, **kwargs):
        self.calls.append(("validate", kwargs))
        return {"valid": True, "connection": {"connection_id": "conn-test"}}

    def create_connection(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"connection": {"connection_id": "conn-test"}, "version": 1}

    def mutate(self, **kwargs):
        self.calls.append(("mutate", kwargs))
        return {
            "operation": kwargs["operation"],
            "resource_id": kwargs["resource_id"],
            "result": {"version": 2},
        }

    def access_preview(self, **kwargs):
        return {"decision": "deny", "reason_codes": ["policy_denied"]}

    def bulk_execute(self, **kwargs):
        return {"plan_digest": "b" * 64, "results": []}


def _app(monkeypatch):
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes, "authorize_route_request", lambda **kwargs: None
    )
    monkeypatch.setattr(routes, "_principal", lambda: _Principal())
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(create_source_control_v1_blueprint(_Api()))
    return app


def _connection_payload(*, dry_run: bool) -> dict[str, object]:
    return {
        "connector_type": "registered_workspace",
        "workspace_id": "workspace-example",
        "display_name": "Example workspace",
        "sensitivity": "internal",
        "dry_run": dry_run,
    }


def test_detail_uses_exact_v1_envelope_and_projection_etag(monkeypatch) -> None:
    app = _app(monkeypatch)
    response = app.test_client().get(
        "/api/source-control/v1/connections/conn-example"
    )

    assert response.status_code == 200
    assert set(response.get_json()) == {"schema", "data"}
    assert (
        response.get_json()["schema"]
        == "ananta.source-control.api-response.v1"
    )
    assert response.headers["ETag"] == "a" * 64


def test_unknown_object_is_uniform_404_not_success(monkeypatch) -> None:
    app = _app(monkeypatch)
    response = app.test_client().get(
        "/api/source-control/v1/connections/missing"
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "schema": "ananta.source-control.error.v1",
        "error": {"code": "source_control_not_found"},
    }


def test_common_policy_denial_stops_collection_before_service(
    monkeypatch,
) -> None:
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes,
        "authorize_route_request",
        lambda **kwargs: (
            {
                "schema": "ananta.source-control.error.v1",
                "error": {"code": "source_control_forbidden"},
            },
            403,
        ),
    )
    monkeypatch.setattr(routes, "_principal", lambda: _Principal())
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(create_source_control_v1_blueprint(_Api()))

    response = app.test_client().get(
        "/api/source-control/v1/connections"
    )

    assert response.status_code == 403
    assert (
        response.get_json()["error"]["code"]
        == "source_control_forbidden"
    )


def test_create_requires_idempotency_but_no_if_match(monkeypatch) -> None:
    app = _app(monkeypatch)
    client = app.test_client()

    missing_key = client.post(
        "/api/source-control/v1/connections",
        json=_connection_payload(dry_run=False),
    )
    created = client.post(
        "/api/source-control/v1/connections",
        json=_connection_payload(dry_run=False),
        headers={"Idempotency-Key": "create-example"},
    )

    assert missing_key.status_code == 428
    assert created.status_code == 201


def test_client_cannot_claim_scope_or_destination_security(monkeypatch) -> None:
    app = _app(monkeypatch)
    client = app.test_client()
    connection = _connection_payload(dry_run=True)
    connection["tenant_id"] = "other-tenant"

    connection_response = client.post(
        "/api/source-control/v1/connections/validate",
        json=connection,
    )
    preview_response = client.post(
        "/api/source-control/v1/access/preview",
        json={
            "source_revision_id": "revision-example",
            "destination_id": "destination-example",
            "operation": "analyze",
            "transformation": "redacted",
            "purpose": "code_review",
            "provider_location": "external_region",
        },
    )

    assert connection_response.status_code == 400
    assert preview_response.status_code == 400
    assert (
        connection_response.get_json()["error"]["code"]
        == "request_fields_forbidden"
    )


def test_connection_rejects_browser_digest_url_and_path(monkeypatch) -> None:
    app = _app(monkeypatch)
    base = _connection_payload(dry_run=True)
    client = app.test_client()

    for forbidden in (
        {"connection_identity_digest": "a" * 64},
        {"url": "https://attacker.invalid/repository"},
        {"path": "/etc"},
    ):
        response = client.post(
            "/api/source-control/v1/connections/validate",
            json={**base, **forbidden},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == (
            "request_fields_forbidden"
        )

    traversal = client.post(
        "/api/source-control/v1/connections/validate",
        json={**base, "relative_path": "../escape"},
    )
    assert traversal.status_code == 400
    assert traversal.get_json()["error"]["code"] == (
        "workspace_relative_path_invalid"
    )

    safe_relative = client.post(
        "/api/source-control/v1/connections/validate",
        json={**base, "relative_path": "src/agent"},
    )
    assert safe_relative.status_code == 200


def test_existing_mutation_requires_dry_run_if_match_and_key(
    monkeypatch,
) -> None:
    app = _app(monkeypatch)
    client = app.test_client()
    path = "/api/source-control/v1/connections/conn-example/disable"

    missing = client.post(path, json={"dry_run": False})
    wrong_dry_run = client.post(
        path,
        json={"dry_run": True},
        headers={
            "If-Match": "a" * 64,
            "Idempotency-Key": "disable-example",
        },
    )
    accepted = client.post(
        path,
        json={"dry_run": False},
        headers={
            "If-Match": "a" * 64,
            "Idempotency-Key": "disable-example",
        },
    )

    assert missing.status_code == 428
    assert wrong_dry_run.status_code == 400
    assert accepted.status_code == 200


def test_unexpected_exception_never_crosses_versioned_boundary(
    monkeypatch,
) -> None:
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes, "authorize_route_request", lambda **kwargs: None
    )
    monkeypatch.setattr(routes, "_principal", lambda: _Principal())
    api = _Api()
    api.raise_detail = True
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(create_source_control_v1_blueprint(api))

    response = app.test_client().get(
        "/api/source-control/v1/connections/conn-example"
    )

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == (
        "source_control_internal_error"
    )
    assert "do-not-leak" not in response.get_data(as_text=True)
