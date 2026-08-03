from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from agent.models.organization_models import OrganizationInstantiationResult
from agent.routes import organization_instances as routes
from agent.services.organization_blueprint_instantiation_service import (
    OrganizationInstantiationError,
)
from agent.services.organization_event_service import (
    InMemoryOrganizationEventStore,
    OrganizationEventService,
)
from agent.services.project_access_authority import ProjectCapability

PLAN_DIGEST = "a" * 64
DEFINITION_REVISION = "b" * 64


@pytest.fixture()
def app() -> Flask:
    return Flask(__name__)


def _result(*, replayed: bool) -> OrganizationInstantiationResult:
    return OrganizationInstantiationResult(
        organization_id="organization-1",
        definition_revision=DEFINITION_REVISION,
        plan_digest=PLAN_DIGEST,
        topology_snapshot_hash="c" * 64,
        team_ids=["team-1"],
        unit_ids=["unit-1"],
        role_slot_ids=["slot-1"],
        relation_ids=["relation-1"],
        organization_admin_grant_id="grant-persistent-1",
        idempotent_replay=replayed,
    )


def _install_route_dependencies(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    *,
    instance_service: Any,
    compile_service: Any,
) -> list[dict[str, Any]]:
    def require_scope(capability: ProjectCapability, *, payload_project_id: object | None = None):
        assert capability is ProjectCapability.MANAGE
        assert payload_project_id == "project-1"
        return (
            SimpleNamespace(subject_id="operator-1"),
            SimpleNamespace(tenant_id="tenant-1", project_id="project-1"),
        )

    runtime_events: list[dict[str, Any]] = []
    event_service = OrganizationEventService(store=InMemoryOrganizationEventStore())

    class RuntimeService:
        def __init__(self, **scope: Any) -> None:
            self._scope = scope

        def emit_event(self, **event: Any) -> None:
            runtime_events.append(
                {
                    "scope": self._scope,
                    "event": event_service.emit(
                        organization_id=self._scope["organization_id"],
                        **event,
                    ),
                }
            )

    monkeypatch.setattr(routes, "require_project_scope", require_scope)
    monkeypatch.setattr(routes, "OrganizationRuntimeApplicationService", RuntimeService)
    app.extensions["organization_instance_application_service"] = instance_service
    app.extensions["organization_compile_application_service"] = compile_service
    app.extensions["organization_read_service"] = SimpleNamespace(
        organization_summary=lambda **_scope: {"organization_id": "organization-1"}
    )
    return runtime_events


def _request(app: Flask):
    return app.test_request_context(
        "/api/organizations",
        method="POST",
        headers={
            "Idempotency-Key": "instantiate-key-1",
            "If-Match": f'"{DEFINITION_REVISION}"',
            "X-Organization-Admin-Grant": "grant-precreation-1",
            "X-Plan-Digest": PLAN_DIGEST,
        },
        json={
            "project_id": "project-1",
            "title": "Organization One",
            "admin_grant": "grant-precreation-1",
            "compile_plan": {
                "compile_token": "expired-compile-token",
                "organization_id": "organization-1",
                "definition_revision": DEFINITION_REVISION,
                "plan_digest": PLAN_DIGEST,
                "admin_policy_hash": "policy-1",
            },
        },
    )


def test_applied_replay_is_returned_after_signed_binding_before_ttl_recompile(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class InstanceService:
        def recover_applied_instantiation(self, **binding: Any):
            calls.append(("recover", binding))
            return _result(replayed=True)

        def instantiate(self, **_kwargs: Any):  # pragma: no cover - forbidden path
            raise AssertionError("replay must not instantiate again")

    class CompileService:
        def verify_replay_binding(self, **_binding: Any):
            return {
                "organization_id": "organization-1",
                "definition_revision": DEFINITION_REVISION,
                "plan_digest": PLAN_DIGEST,
                "admin_policy_hash": "policy-1",
                "title": "Organization One",
            }

        def recompile_bound_plan(self, **_kwargs: Any):  # pragma: no cover - forbidden path
            raise AssertionError("replay must precede TTL and catalog recompile")

    runtime_events = _install_route_dependencies(
        app,
        monkeypatch,
        instance_service=InstanceService(),
        compile_service=CompileService(),
    )

    with _request(app):
        response, status = routes.instantiate_organization.__wrapped__()
    with _request(app):
        replay_response, replay_status = routes.instantiate_organization.__wrapped__()

    assert status == 200
    assert replay_status == 200
    assert response.get_json()["data"]["replayed"] is True
    assert replay_response.get_json() == response.get_json()
    expected_binding = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "plan_digest": PLAN_DIGEST,
        "name": "Organization One",
        "idempotency_key": "instantiate-key-1",
        "principal_id": "operator-1",
        "expected_organization_id": "organization-1",
        "expected_definition_revision": DEFINITION_REVISION,
        "grant_id": "grant-precreation-1",
    }
    assert calls == [("recover", expected_binding), ("recover", expected_binding)]
    assert runtime_events[0]["event"].definition_revision == DEFINITION_REVISION
    assert runtime_events[0]["event"].event_id == runtime_events[1]["event"].event_id
    assert runtime_events[0]["event"].sequence == runtime_events[1]["event"].sequence == 1


def test_missing_operation_continues_through_existing_compile_and_create_flow(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    plan = SimpleNamespace(
        definition_revision=DEFINITION_REVISION,
        plan_digest=PLAN_DIGEST,
    )

    class InstanceService:
        def recover_applied_instantiation(self, **_binding: Any):
            order.append("recover")
            return None

        def instantiate(self, **_binding: Any):
            order.append("instantiate")
            return _result(replayed=False)

    class CompileService:
        def verify_replay_binding(self, **_binding: Any):
            return {
                "organization_id": "organization-1",
                "definition_revision": DEFINITION_REVISION,
                "plan_digest": PLAN_DIGEST,
                "admin_policy_hash": "policy-1",
                "title": "Organization One",
            }

        def recompile_bound_plan(self, **_binding: Any):
            order.append("compile")
            return plan, {
                "title": "Organization One",
                "admin_policy_hash": "policy-1",
            }

    _install_route_dependencies(
        app,
        monkeypatch,
        instance_service=InstanceService(),
        compile_service=CompileService(),
    )

    with _request(app):
        _response, status = routes.instantiate_organization.__wrapped__()

    assert status == 201
    assert order == ["recover", "compile", "instantiate"]


@pytest.mark.parametrize(
    "reason_code",
    ("organization_idempotency_key_conflict", "organization_instantiation_in_progress"),
)
def test_present_non_replayable_operation_fails_closed_before_compile(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str,
) -> None:
    class InstanceService:
        def recover_applied_instantiation(self, **_binding: Any):
            raise OrganizationInstantiationError(reason_code)

    class CompileService:
        def verify_replay_binding(self, **_binding: Any):
            return {
                "organization_id": "organization-1",
                "definition_revision": DEFINITION_REVISION,
                "plan_digest": PLAN_DIGEST,
                "admin_policy_hash": "policy-1",
                "title": "Organization One",
            }

        def recompile_bound_plan(self, **_kwargs: Any):  # pragma: no cover - forbidden path
            raise AssertionError("conflicted operation must not reach compile")

    _install_route_dependencies(
        app,
        monkeypatch,
        instance_service=InstanceService(),
        compile_service=CompileService(),
    )

    with _request(app):
        response, status = routes.instantiate_organization.__wrapped__()

    assert status == 409
    assert response.get_json()["message"] == reason_code
