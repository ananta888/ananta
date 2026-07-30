from __future__ import annotations

from flask import Flask

import agent.auth as auth
from agent.routes.source_control_access import (
    record_source_control_route_denial,
)
from agent.routes.source_control_v1 import create_source_control_v1_blueprint
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceControlAction,
)


class _Audit:
    def __init__(self) -> None:
        self.events = []

    def record_denial(self, event) -> None:
        self.events.append(dict(event))


class _Api:
    def binding(self, **kwargs):
        return None

    def list_connections(self, **kwargs):
        return {"items": [], "next_cursor": None}


def test_foreign_reference_is_hashed_in_content_free_audit() -> None:
    app = Flask(__name__)
    audit = _Audit()
    app.extensions["source_control_route_deny_audit"] = audit
    with app.test_request_context(
        "/api/source-control/v1/connections/foreign-object",
        headers={"X-Request-ID": "trace-example"},
    ):
        record_source_control_route_denial(
            principal=HubSourcePrincipal(
                subject_id="actor-example",
                tenant_id="tenant-example",
                project_id="project-example",
                roles=frozenset({"project_owner"}),
            ),
            action=SourceControlAction.detail,
            resource_kind="source_connection",
            object_id="foreign-object",
            status_code=404,
            reason_code="source_control_not_found",
        )

    event = audit.events[0]
    assert event["outcome"] == "deny"
    assert event["status_code"] == 404
    assert event["trace_id"] == "trace-example"
    assert event["resource_reference"] != "foreign-object"
    assert "foreign-object" not in str(event)


def test_unauthenticated_v1_request_is_audited_before_runtime(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    audit = _Audit()
    app.extensions["source_control_route_deny_audit"] = audit
    app.register_blueprint(create_source_control_v1_blueprint(_Api()))

    response = app.test_client().get(
        "/api/source-control/v1/connections",
        headers={"X-Request-ID": "trace-unauthenticated"},
    )

    assert response.status_code == 401
    assert audit.events[-1]["reason_code"] == "authentication_required"
    assert audit.events[-1]["actor_id"] == "anonymous"
