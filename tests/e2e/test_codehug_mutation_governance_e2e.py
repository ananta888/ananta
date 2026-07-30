from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

import agent.routes.source_control_v1 as routes
from agent.routes.source_control_v1 import create_source_control_v1_blueprint


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "owner-example"
    tenant_id: str = "tenant-example"
    project_id: str = "project-example"
    roles: frozenset[str] = frozenset({"project_owner"})


class _Api:
    def __init__(self) -> None:
        self.calls = []

    def binding(self, **kwargs):
        return None

    def codehug_mutation(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "schema": "ananta.codehug.mutation-result.v1",
            "status": "accepted",
            "operation_id": "operation-example",
            "binding_digest": "e" * 64,
        }


def _app(monkeypatch):
    api = _Api()
    monkeypatch.setattr(routes, "check_auth", lambda view: view)
    monkeypatch.setattr(
        routes, "authorize_route_request", lambda **kwargs: None
    )
    monkeypatch.setattr(routes, "_principal", lambda: _Principal())
    app = Flask(__name__)
    app.register_blueprint(create_source_control_v1_blueprint(api))
    return app, api


def test_direct_tamper_cannot_supply_write_or_security_bindings(
    monkeypatch,
) -> None:
    app, api = _app(monkeypatch)
    client = app.test_client()
    path = "/api/source-control/v1/codehug/mutations"
    headers = {"Idempotency-Key": "codehug-e2e-example"}

    for tamper in (
        {"write_armed": True},
        {"tool_id": "tool-attacker"},
        {"source_revision_id": "revision-attacker"},
        {"destination_id": "destination-attacker"},
        {"operation": "write"},
        {"transformation": "raw"},
        {"approval_id": "approval-attacker"},
    ):
        response = client.post(
            path,
            headers=headers,
            json={
                "mutation_intent_id": "intent-example",
                "dry_run": False,
                **tamper,
            },
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == (
            "request_fields_forbidden"
        )
    assert api.calls == []


def test_only_server_intent_id_reaches_hub_composition(monkeypatch) -> None:
    app, api = _app(monkeypatch)
    response = app.test_client().post(
        "/api/source-control/v1/codehug/mutations",
        headers={"Idempotency-Key": "codehug-e2e-example"},
        json={
            "mutation_intent_id": "intent-example",
            "dry_run": False,
        },
    )

    assert response.status_code == 202
    assert api.calls[0]["mutation_intent_id"] == "intent-example"
    assert set(api.calls[0]) == {
        "principal",
        "mutation_intent_id",
        "idempotency_key",
    }
