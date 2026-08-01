from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from flask import Flask
import pytest

import agent.routes.source_control_v1 as routes
from agent.routes.source_control_v1 import (
    create_source_control_v1_blueprint,
)
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.project_access_fakes import AllowProjectAccess


_SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
_ERROR_SCHEMA = "ananta.source-control.error.v1"


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "owner-example"
    tenant_id: str = "tenant-from-auth"
    project_id: str = "project-example"
    roles: frozenset[str] = frozenset({"project_owner"})


class _RecordingApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def binding(self, *, resource_kind, resource_id):
        return {
            "tenant_id": "tenant-from-auth",
            "project_id": "project-example",
            "owner_id": "owner-example",
        }

    def validate_content_admission(self, **kwargs):
        self.calls.append(("validate_content_admission", kwargs))
        return {
            "valid": True,
            "preview": {"manifest_digest": "a" * 64},
        }

    def create_content_admission(self, **kwargs):
        self.calls.append(("create_content_admission", kwargs))
        return {
            "connection": {"connection_id": "connection-content"},
            "revision": {"source_revision_id": "revision-content"},
        }

    def list_source_control_catalog(self, **kwargs):
        self.calls.append(("list_source_control_catalog", kwargs))
        return {
            "items": [{"catalog": kwargs["catalog"]}],
            "next_cursor": None,
            "capabilities": {"read_only": True},
        }

    def list_grant_presets(self, **kwargs):
        self.calls.append(("list_grant_presets", kwargs))
        return {
            "items": [{"preset_id": "preset-review"}],
            "next_cursor": None,
            "capabilities": {"read_only": True},
        }

    def list_grants(self, **kwargs):
        self.calls.append(("list_grants", kwargs))
        return {
            "items": [{"grant_id": "grant-existing"}],
            "next_cursor": None,
            "capabilities": {"read_only": True},
        }

    def create_grant(self, **kwargs):
        self.calls.append(("create_grant", kwargs))
        return {
            "grant": {
                "grant_id": "grant-created",
                "etag": "c" * 64,
            }
        }

    def revoke_grant(self, **kwargs):
        self.calls.append(("revoke_grant", kwargs))
        return {
            "grant": {
                "grant_id": kwargs["grant_id"],
                "state": "revoked",
                "etag": "d" * 64,
            }
        }


def _app(monkeypatch):
    principal = _Principal()
    api = _RecordingApi()
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes, "authorize_route_request", lambda **kwargs: None
    )
    monkeypatch.setattr(routes, "_principal", lambda: principal)
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(create_source_control_v1_blueprint(api))
    return app, api, principal


def _assert_scoped_principal(
    actual: object,
    *,
    authenticated: _Principal,
) -> None:
    assert isinstance(actual, HubSourcePrincipal)
    assert actual.subject_id == authenticated.subject_id
    assert actual.tenant_id == authenticated.tenant_id
    assert actual.project_id == "project-example"
    assert actual.roles == frozenset({"project_owner"})


def _assert_recorded_call(
    api: _RecordingApi,
    index: int,
    *,
    authenticated: _Principal,
    name: str,
    kwargs: dict[str, object],
) -> None:
    recorded_name, recorded_kwargs = api.calls[index]
    assert recorded_name == name
    _assert_scoped_principal(
        recorded_kwargs["principal"],
        authenticated=authenticated,
    )
    assert {
        key: value
        for key, value in recorded_kwargs.items()
        if key != "principal"
    } == kwargs


def _assert_error(response, *, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.get_json() == {
        "schema": _ERROR_SCHEMA,
        "error": {"code": code},
    }


def test_content_admission_uses_exact_envelopes_and_passes_exact_dtos(
    monkeypatch,
) -> None:
    app, api, principal = _app(monkeypatch)
    client = app.test_client()
    payload = {
        "project_id": "project-example",
        "source_type": "direct_text",
        "display_name": "Architecture notes",
        "sensitivity": "internal",
        "content": "Hub owns orchestration.",
        "media_type": "text/plain",
        "dry_run": True,
    }

    validated = client.post(
        "/api/source-control/v1/content-admissions/validate",
        json=payload,
    )
    create_payload = {**payload, "dry_run": False}
    created = client.post(
        "/api/source-control/v1/content-admissions",
        json=create_payload,
        headers={"Idempotency-Key": "content-create-001"},
    )

    assert validated.status_code == 200
    assert validated.get_json() == {
        "schema": _SUCCESS_SCHEMA,
        "data": {
            "valid": True,
            "preview": {"manifest_digest": "a" * 64},
        },
    }
    assert created.status_code == 201
    assert created.get_json() == {
        "schema": _SUCCESS_SCHEMA,
        "data": {
            "connection": {"connection_id": "connection-content"},
            "revision": {"source_revision_id": "revision-content"},
        },
    }
    assert api.calls == [
        (
            "validate_content_admission",
            {"principal": principal, "payload": payload},
        ),
        (
            "create_content_admission",
            {
                "principal": principal,
                "payload": create_payload,
                "idempotency_key": "content-create-001",
            },
        ),
    ]


def test_content_admission_create_requires_idempotency_key(
    monkeypatch,
) -> None:
    app, api, _ = _app(monkeypatch)

    response = app.test_client().post(
        "/api/source-control/v1/content-admissions",
        json={
            "project_id": "project-example",
            "source_type": "direct_text",
            "display_name": "Notes",
            "sensitivity": "internal",
            "content": "bounded",
            "media_type": "text/plain",
            "dry_run": False,
        },
    )

    _assert_error(
        response,
        status=428,
        code="idempotency_key_required",
    )
    assert api.calls == []


_READ_CASES = (
    (
        "/api/source-control/v1/workspaces",
        "list_source_control_catalog",
        "workspaces",
        {"q": "workspace", "enabled": "true"},
    ),
    (
        "/api/source-control/v1/registered-remotes",
        "list_source_control_catalog",
        "registered_remotes",
        {"q": "repository", "kind": "github", "state": "active"},
    ),
    (
        "/api/source-control/v1/index-profiles",
        "list_source_control_catalog",
        "index_profiles",
        {"q": "default", "source": "builtin"},
    ),
    (
        "/api/source-control/v1/grant-presets",
        "list_grant_presets",
        None,
        {"q": "review", "operation": "analyze", "transformation": "raw"},
    ),
    (
        "/api/source-control/v1/grants",
        "list_grants",
        None,
        {
            "state": "active",
            "source_revision_id": "revision-example",
            "destination_id": "destination-example",
        },
    ),
)


@pytest.mark.parametrize(
    ("path", "call_name", "catalog", "filters"),
    _READ_CASES,
)
def test_read_catalogs_are_scoped_and_pass_bounded_queries(
    monkeypatch,
    path,
    call_name,
    catalog,
    filters,
) -> None:
    app, api, principal = _app(monkeypatch)
    query = urlencode(
        {
            "project_id": "project-example",
            "cursor": "opaque-cursor",
            "limit": 25,
            **filters,
        }
    )

    response = app.test_client().get(f"{path}?{query}")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"schema", "data"}
    assert body["schema"] == _SUCCESS_SCHEMA
    assert body["data"]["next_cursor"] is None
    assert body["data"]["capabilities"] == {"read_only": True}
    call, kwargs = api.calls[-1]
    assert call == call_name
    _assert_scoped_principal(
        kwargs["principal"],
        authenticated=principal,
    )
    assert {
        key: value
        for key, value in kwargs.items()
        if key != "principal"
    } == {
        **({"catalog": catalog} if catalog is not None else {}),
        "project_id": "project-example",
        "cursor": "opaque-cursor",
        "limit": 25,
        "filters": filters,
    }
    assert kwargs["principal"].tenant_id == "tenant-from-auth"


@pytest.mark.parametrize(
    "path",
    [case[0] for case in _READ_CASES],
)
def test_read_catalogs_require_matching_project_from_auth_scope(
    monkeypatch,
    path,
) -> None:
    app, api, _ = _app(monkeypatch)
    client = app.test_client()

    missing = client.get(path)
    mismatched = client.get(f"{path}?project_id=project-other")

    _assert_error(missing, status=400, code="project_id_required")
    _assert_error(
        mismatched,
        status=404,
        code="source_control_not_found",
    )
    assert api.calls == []


@pytest.mark.parametrize(
    "path",
    [case[0] for case in _READ_CASES],
)
def test_read_catalogs_reject_unbounded_and_tenant_query_fields(
    monkeypatch,
    path,
) -> None:
    app, api, _ = _app(monkeypatch)
    client = app.test_client()

    too_large = client.get(
        f"{path}?project_id=project-example&limit=201"
    )
    tenant_from_browser = client.get(
        f"{path}?project_id=project-example&tenant_id=tenant-other"
    )

    _assert_error(too_large, status=400, code="limit_invalid")
    _assert_error(
        tenant_from_browser,
        status=400,
        code="query_fields_forbidden",
    )
    assert api.calls == []


def _grant_payload() -> dict[str, object]:
    return {
        "source_revision_id": "revision-example",
        "destination_id": "destination-example",
        "policy_id": "policy-example",
        "preset_id": "preset-review",
        "duration_seconds": 3600,
    }


def test_grant_create_requires_headers_and_returns_projection_etag(
    monkeypatch,
) -> None:
    app, api, principal = _app(monkeypatch)
    client = app.test_client()
    path = "/api/source-control/v1/grants?project_id=project-example"
    payload = _grant_payload()

    missing_if_match = client.post(
        path,
        json=payload,
        headers={"Idempotency-Key": "grant-create-001"},
    )
    missing_key = client.post(
        path,
        json=payload,
        headers={"If-Match": '"base-etag"'},
    )
    created = client.post(
        path,
        json=payload,
        headers={
            "If-Match": '"base-etag"',
            "Idempotency-Key": "grant-create-001",
        },
    )

    _assert_error(
        missing_if_match,
        status=428,
        code="if_match_required",
    )
    _assert_error(
        missing_key,
        status=428,
        code="idempotency_key_required",
    )
    assert created.status_code == 201
    assert created.headers["ETag"] == f'"{"c" * 64}"'
    assert created.get_json() == {
        "schema": _SUCCESS_SCHEMA,
        "data": {
            "grant": {
                "grant_id": "grant-created",
                "etag": "c" * 64,
            }
        },
    }
    assert len(api.calls) == 1
    _assert_recorded_call(
        api,
        0,
        authenticated=principal,
        name="create_grant",
        kwargs={
            "project_id": "project-example",
            "payload": payload,
            "if_match": '"base-etag"',
            "idempotency_key": "grant-create-001",
        },
    )


def test_grant_create_rejects_non_contract_body_and_query(
    monkeypatch,
) -> None:
    app, api, _ = _app(monkeypatch)
    client = app.test_client()
    headers = {
        "If-Match": '"base-etag"',
        "Idempotency-Key": "grant-create-001",
    }

    body_with_browser_scope = client.post(
        "/api/source-control/v1/grants?project_id=project-example",
        json={**_grant_payload(), "tenant_id": "tenant-other"},
        headers=headers,
    )
    query_with_browser_scope = client.post(
        (
            "/api/source-control/v1/grants"
            "?project_id=project-example&tenant_id=tenant-other"
        ),
        json=_grant_payload(),
        headers=headers,
    )

    _assert_error(
        body_with_browser_scope,
        status=400,
        code="request_fields_forbidden",
    )
    _assert_error(
        query_with_browser_scope,
        status=400,
        code="query_fields_forbidden",
    )
    assert api.calls == []


def test_grant_revoke_requires_headers_and_passes_exact_body(
    monkeypatch,
) -> None:
    app, api, principal = _app(monkeypatch)
    client = app.test_client()
    path = (
        "/api/source-control/v1/grants/grant-existing/actions/revoke"
        "?project_id=project-example"
    )
    payload = {"reason_code": "operator_request"}

    missing_if_match = client.post(
        path,
        json=payload,
        headers={"Idempotency-Key": "grant-revoke-001"},
    )
    missing_key = client.post(
        path,
        json=payload,
        headers={"If-Match": '"current-etag"'},
    )
    revoked = client.post(
        path,
        json=payload,
        headers={
            "If-Match": '"current-etag"',
            "Idempotency-Key": "grant-revoke-001",
        },
    )

    _assert_error(
        missing_if_match,
        status=428,
        code="if_match_required",
    )
    _assert_error(
        missing_key,
        status=428,
        code="idempotency_key_required",
    )
    assert revoked.status_code == 200
    assert revoked.headers["ETag"] == f'"{"d" * 64}"'
    assert revoked.get_json() == {
        "schema": _SUCCESS_SCHEMA,
        "data": {
            "grant": {
                "grant_id": "grant-existing",
                "state": "revoked",
                "etag": "d" * 64,
            }
        },
    }
    assert len(api.calls) == 1
    _assert_recorded_call(
        api,
        0,
        authenticated=principal,
        name="revoke_grant",
        kwargs={
            "project_id": "project-example",
            "grant_id": "grant-existing",
            "payload": payload,
            "if_match": '"current-etag"',
            "idempotency_key": "grant-revoke-001",
        },
    )
