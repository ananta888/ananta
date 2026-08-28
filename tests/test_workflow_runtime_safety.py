from __future__ import annotations

import ast
import uuid
from pathlib import Path

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.visual_process import vp_bp
from agent.routes.workflow_adapters import workflow_adapters_bp
from agent.services.workflow_backend_factory import (
    WorkflowBackendConfig,
    WorkflowBackendConfigurationError,
    get_workflow_backend,
)
from agent.services.workflow_control_composition import (
    reset_workflow_backend_control_facade,
)
from agent.services.workflow_route_authorization_service import workflow_route_authorization_service
from agent.services.workflow_runtime.streaming import (
    WorkflowStreamBatch,
    WorkflowStreamFrame,
)
from agent.services.workflow_runtime_fallback_policy import (
    RuntimeFallbackRequest,
    workflow_runtime_fallback_policy,
)


class _AdmittedTestReleaseEvidence:
    """Isolate route-security tests from the production release artifact."""

    def evaluate(self, **_values):
        return True, "runtime_release_test_evidence_verified"


def _admit_test_runtime_release(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_release_admission",
        lambda _backend: _AdmittedTestReleaseEvidence(),
    )
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_rollout_policies",
        lambda: None,
    )
    reset_workflow_backend_control_facade()


def _workflow_app(*, agent_token: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=agent_token)
    app.register_blueprint(vp_bp)
    app.register_blueprint(workflow_adapters_bp)
    workflow_route_authorization_service.clear()
    return app


def _user_headers(*, subject: str, tenant_id: str) -> dict[str, str]:
    token = generate_token(
        {"sub": subject, "tenant_id": tenant_id, "role": "user"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def _workflow_payload(workflow_id: str, *, gate: bool = False) -> dict:
    return {
        "workflow_request": {
            "workflow_id": workflow_id,
            "workflow_type": "security_test",
            "steps": [
                {
                    "step_id": "step-1",
                    "task_kind": "coding",
                    "gate": gate,
                    "policy_scope": {"source": "security-test"},
                }
            ],
            "policy_scope": {"source": "security-test"},
        }
    }


def test_unknown_backend_is_rejected_without_local_fallback():
    config = WorkflowBackendConfig(backend="typo-runtime")

    try:
        get_workflow_backend(config)
    except WorkflowBackendConfigurationError as exc:
        assert exc.reason_code == "workflow_backend_unknown"
        assert exc.backend == "typo-runtime"
        assert exc.fallback_decision is not None
        assert exc.fallback_decision.allowed is False
        assert exc.fallback_decision.request.target_runtime == "local"
    else:  # pragma: no cover - explicit assertion message is clearer than pytest.raises here
        raise AssertionError("unknown backend unexpectedly selected a runtime")


def test_fallback_policy_blocks_protected_capability_loss_even_when_enabled():
    decision = workflow_runtime_fallback_policy.evaluate(
        RuntimeFallbackRequest.create(
            source_runtime="durable",
            target_runtime="ephemeral",
            reason_code="durable_unavailable",
            semantic_class="degraded",
            source_capabilities={"policy", "audit", "durability", "resume"},
            target_capabilities={"policy", "audit"},
            explicitly_enabled=True,
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "runtime_fallback_protected_capability_loss"
    assert decision.protected_capability_loss == ("durability", "resume")


def test_workflow_control_routes_require_real_bearer_auth_when_auth_is_disabled():
    client = _workflow_app(agent_token=None).test_client()

    started = client.post("/api/visual-process/workflow/start", json=_workflow_payload("wf-no-auth"))
    status = client.get("/api/visual-process/workflow/wf-no-auth/status")
    events = client.get("/api/visual-process/workflow/wf-no-auth/events")
    signal = client.post("/api/visual-process/workflow/wf-no-auth/signal", json={"name": "approve"})
    cancel = client.post("/api/visual-process/workflow/wf-no-auth/cancel")

    assert {response.status_code for response in (started, status, events, signal, cancel)} == {401}


def test_workflow_run_access_is_bound_to_subject_and_tenant(
    monkeypatch,
    workflow_runtime_auth_keyring_file,
):
    del workflow_runtime_auth_keyring_file
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    _admit_test_runtime_release(monkeypatch)
    client = _workflow_app(agent_token=None).test_client()
    owner = _user_headers(subject="owner", tenant_id="tenant-a")
    foreign_tenant = _user_headers(subject="owner", tenant_id="tenant-b")
    foreign_subject = _user_headers(subject="other", tenant_id="tenant-a")

    started = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload("wf-owner-bound"),
        headers=owner,
    )
    assert started.status_code == 200, started.get_json()

    assert (
        client.get(
            "/api/visual-process/workflow/wf-owner-bound/status",
            headers=owner,
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/visual-process/workflow/wf-owner-bound/status",
            headers=foreign_tenant,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/visual-process/workflow/wf-owner-bound/cancel",
            headers=foreign_subject,
        ).status_code
        == 404
    )


def test_workflow_signal_is_bounded_validated_and_secret_redacted(
    monkeypatch,
    workflow_runtime_auth_keyring_file,
):
    del workflow_runtime_auth_keyring_file
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    _admit_test_runtime_release(monkeypatch)
    client = _workflow_app(agent_token=None).test_client()
    owner = _user_headers(subject="signal-owner", tenant_id="tenant-signal")
    workflow_id = f"wf-signal-bounds-{uuid.uuid4().hex}"
    started = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload(workflow_id, gate=True),
        headers=owner,
    )
    assert started.status_code == 200, started.get_json()

    malformed = client.post(
        f"/api/visual-process/workflow/{workflow_id}/signal",
        data="not-json",
        headers={**owner, "Content-Type": "application/json"},
    )
    oversized = client.post(
        f"/api/visual-process/workflow/{workflow_id}/signal",
        data="x" * (16 * 1024 + 1),
        headers={**owner, "Content-Type": "application/json"},
    )
    invalid_payload = client.post(
        f"/api/visual-process/workflow/{workflow_id}/signal",
        json={"name": "approve", "payload": ["not", "an", "object"]},
        headers=owner,
    )
    accepted = client.post(
        f"/api/visual-process/workflow/{workflow_id}/signal",
        json={"name": "approve", "actor": "spoofed", "payload": {"api_key": "secret-value"}},
        headers=owner,
    )

    assert malformed.status_code == 400
    assert oversized.status_code == 413
    assert invalid_payload.status_code == 422
    assert accepted.status_code == 200, accepted.get_json()
    events = accepted.get_json()["events"]
    signal_event = next(
        (
            event
            for event in events
            if event["event_type"] == "workflow.approval.granted"
        ),
        None,
    )
    assert signal_event is not None, [event["event_type"] for event in events]
    assert signal_event["actor"] == "runtime-source"
    assert "spoofed" not in str(signal_event)
    assert "secret-value" not in str(signal_event)
    assert "api_key" not in str(signal_event)


def test_invalid_backend_returns_stable_503_and_releases_run_reservation(monkeypatch):
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "invalid-backend")
    client = _workflow_app(agent_token=None).test_client()
    owner = _user_headers(subject="backend-owner", tenant_id="tenant-backend")

    started = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload("wf-invalid-backend"),
        headers=owner,
    )

    assert started.status_code == 503
    assert started.get_json()["data"]["reason_code"] == "workflow_backend_invalid"
    status = client.get(
        "/api/visual-process/workflow/wf-invalid-backend/status",
        headers=owner,
    )
    assert status.status_code == 404


def test_temporal_selection_does_not_open_unauthenticated_control_paths(monkeypatch):
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "temporal")
    client = _workflow_app(agent_token=None).test_client()

    response = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload("wf-temporal-no-auth"),
    )

    assert response.status_code == 401


def test_temporal_degraded_start_returns_stable_non_2xx(monkeypatch):
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "temporal")
    monkeypatch.setattr(
        "agent.services.temporal_workflow_backend.TemporalWorkflowBackend._temporal_unavailable",
        staticmethod(lambda: "temporalio_unavailable:ImportError"),
    )
    client = _workflow_app(agent_token=None).test_client()
    owner = _user_headers(subject="temporal-owner", tenant_id="tenant-temporal")

    response = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload("wf-temporal-degraded"),
        headers=owner,
    )

    assert response.status_code == 503
    assert response.get_json()["data"]["reason_code"] == "workflow_backend_unavailable"


def test_hub_adapter_route_has_no_worker_adapter_imports():
    route_path = Path(__file__).parents[1] / "agent" / "routes" / "workflow_adapters.py"
    tree = ast.parse(route_path.read_text(encoding="utf-8"))
    imported_modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported_modules.update(
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    )
    assert not any(module == "worker" or module.startswith("worker.") for module in imported_modules)


def test_adapter_discovery_is_authenticated_read_only_and_sanitized():
    token = "adapter-route-test-token-that-is-long-enough"
    client = _workflow_app(agent_token=token).test_client()
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/workflow_adapters/").status_code == 401
    response = client.get("/api/workflow_adapters/", headers=headers)

    assert response.status_code == 200
    descriptors = response.get_json()["adapters"]
    assert descriptors
    by_kind = {descriptor["kind"]: descriptor for descriptor in descriptors}
    assert by_kind["langgraph"]["status"] == "ready"
    assert by_kind["langgraph"]["reason"] == "hub_task_queue_bridge_ready"
    assert by_kind["langgraph"]["execution_mode"] == "hub_delegated"
    assert by_kind["langchain"]["status"] == "unavailable"
    assert all("provider_diagnostics" not in descriptor for descriptor in descriptors)


def test_adapter_execution_poll_cancel_and_stream_use_the_hub_control_facade(
    monkeypatch,
):
    class BoundControl:
        def submit(self, **_values):
            return {
                "schema": "ananta.workflow-adapter-control.v1",
                "hub_task_id": "wfa-route-test",
                "workflow_id": "wf-route-test",
                "duplicate": False,
            }

        def status(self, **_values):
            return {
                "hub_task_id": "wfa-route-test",
                "workflow_id": "wf-route-test",
                "adapter_kind": "langgraph",
                "status": "created",
            }

        def cancel(self, **_values):
            return {
                "hub_task_id": "wfa-route-test",
                "workflow_id": "wf-route-test",
                "adapter_kind": "langgraph",
                "status": "cancelled",
            }

        def stream(self, **_values):
            return WorkflowStreamBatch(
                frames=(
                    WorkflowStreamFrame(
                        event_type="workflow.run.started",
                        workflow_id="wf-route-test",
                        cursor="cursor-1",
                        event_id="event-1",
                        occurred_at=1.0,
                    ),
                ),
                next_cursor="cursor-1",
                has_more=False,
            )

    class Facade:
        def bind(self, _principal):
            return BoundControl()

    monkeypatch.setattr(
        "agent.routes.workflow_adapters.get_workflow_adapter_control_facade",
        lambda: Facade(),
    )
    token = "adapter-command-test-token-that-is-long-enough"
    client = _workflow_app(agent_token=token).test_client()
    headers = {"Authorization": f"Bearer {token}"}

    execute = client.post(
        "/api/workflow_adapters/langgraph/execute",
        json={"task_id": "t1", "task_type": "agent_workflow"},
        headers=headers,
    )
    legacy_stream = client.get(
        "/api/workflow_adapters/langgraph/stream?task_id=t1&payload=%7B%7D",
        headers=headers,
    )
    post_stream = client.post(
        "/api/workflow_adapters/langgraph/stream",
        json={"hub_task_id": "wfa-route-test"},
        headers=headers,
    )
    status = client.get(
        "/api/workflow_adapters/langgraph/operations/wfa-route-test",
        headers=headers,
    )
    cancelled = client.post(
        "/api/workflow_adapters/langgraph/operations/wfa-route-test/cancel",
        json={"reason": "operator"},
        headers=headers,
    )
    oversized = client.post(
        "/api/workflow_adapters/langgraph/execute",
        data="x" * (64 * 1024 + 1),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert execute.status_code == 202
    assert execute.get_json()["hub_task_id"] == "wfa-route-test"
    assert legacy_stream.status_code == 400
    assert legacy_stream.get_json()["data"]["reason_code"] == "workflow_stream_query_transport_forbidden"
    assert post_stream.status_code == 200
    assert post_stream.mimetype == "application/x-ndjson"
    assert status.status_code == 200
    assert status.get_json()["status"] == "created"
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    assert oversized.status_code == 413
