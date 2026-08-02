from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

import agent.routes.source_control_v1 as routes
from agent.routes.source_control_v1 import create_source_control_v1_blueprint
from tests.project_access_fakes import AllowProjectAccess


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "owner-example"
    tenant_id: str = "tenant-example"
    project_id: str = "project-example"
    roles: frozenset[str] = frozenset({"project_owner"})


class _Api:
    def __init__(self) -> None:
        self.calls = []

    def binding(self, **_kwargs):
        return {
            "tenant_id": "tenant-example",
            "project_id": "project-example",
            "owner_id": "owner-example",
        }

    def prepare_index_access_options(self, **kwargs):
        self.calls.append(("options", kwargs))
        return {
            "connection_id": kwargs["connection_id"],
            "source_revision": {
                "source_revision_id": "srev-example",
                "revision_digest": "a" * 64,
                "admission_state": "admitted",
                "captured_at": "2026-08-01T10:00:00Z",
            },
            "destinations": [],
            "options": [],
            "readiness": {"ready": True, "reason_codes": []},
            "etag": "b" * 64,
        }

    def prepare_index_access(self, **kwargs):
        self.calls.append(("prepare", kwargs))
        return {
            "access_ready": True,
            "connection_id": kwargs["connection_id"],
            "source_revision_id": kwargs["payload"]["source_revision_id"],
            "destination_id": kwargs["payload"]["destination_id"],
            "option_id": kwargs["payload"]["option_id"],
            "effect": {
                "provider_location": "local",
                "transformation": "redacted",
                "one_time": True,
            },
            "policy": {
                "policy_id": "safe-policy",
                "version": 1,
                "state": "active",
                "etag": "c" * 64,
            },
            "grant": {
                "grant_id": "grant-example",
                "state": "active",
                "etag": "d" * 64,
                "expires_at": "2026-08-01T10:15:00Z",
            },
            "next_actions": ["start_index_run"],
        }


def _app(monkeypatch):
    api = _Api()
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes, "authorize_route_request", lambda **_kwargs: None
    )
    monkeypatch.setattr(routes, "_principal", lambda: _Principal())
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(create_source_control_v1_blueprint(api))
    return app.test_client(), api


def _payload():
    return {
        "source_revision_id": "srev-example",
        "destination_id": "destination-example",
        "option_id": "local-redacted-one-time-index",
        "duration_seconds": 900,
        "confirmed": True,
    }


def test_prepare_index_access_route_exposes_canonical_etags(monkeypatch) -> None:
    client, api = _app(monkeypatch)
    path = (
        "/api/source-control/v1/connections/connection-example/actions/"
        "prepare-index-access?project_id=project-example"
    )

    options = client.get(path)
    prepared = client.post(
        path,
        json=_payload(),
        headers={
            "If-Match": options.headers["ETag"],
            "Idempotency-Key": "prepare-route-example",
        },
    )

    assert options.status_code == 200
    assert options.headers["ETag"] == f'"{"b" * 64}"'
    assert options.get_json()["data"]["etag"] == "b" * 64
    assert prepared.status_code == 201
    assert prepared.headers["ETag"] == f'"{"d" * 64}"'
    assert prepared.get_json()["data"]["access_ready"] is True
    assert [name for name, _kwargs in api.calls] == ["options", "prepare"]
    assert api.calls[1][1]["payload"] == _payload()


def test_prepare_index_access_route_rejects_unsafe_intent_shapes(monkeypatch) -> None:
    client, api = _app(monkeypatch)
    path = (
        "/api/source-control/v1/connections/connection-example/actions/"
        "prepare-index-access?project_id=project-example"
    )
    headers = {
        "If-Match": f'"{"b" * 64}"',
        "Idempotency-Key": "prepare-route-example",
    }

    unconfirmed = client.post(
        path, json={**_payload(), "confirmed": False}, headers=headers
    )
    client_policy = client.post(
        path,
        json={**_payload(), "policy_id": "browser-policy"},
        headers=headers,
    )
    missing_headers = client.post(path, json=_payload())

    assert unconfirmed.status_code == 400
    assert unconfirmed.get_json()["error"]["code"] == (
        "index_access_confirmation_required"
    )
    assert client_policy.status_code == 400
    assert client_policy.get_json()["error"]["code"] == (
        "request_fields_forbidden"
    )
    assert missing_headers.status_code == 428
    assert api.calls == []
