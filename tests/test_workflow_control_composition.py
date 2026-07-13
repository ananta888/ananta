from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.visual_process import vp_bp
from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowRequest,
    WorkflowSignal,
    WorkflowStepRequest,
)
from agent.services.workflow_control_composition import (
    DURABLE_RUN_SIGNAL_SCHEMA,
    UnavailableWorkflowRuntimeReleaseAdmission,
    WorkflowBackendDurableRunAdapter,
    build_workflow_backend_control_facade,
    get_workflow_backend_control_facade,
    reset_workflow_backend_control_facade,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)

ROOT = Path(__file__).resolve().parents[1]


class RecordingBackend:
    def __init__(self, backend_id: str = "local") -> None:
        self.backend_id = backend_id
        self.starts = 0
        self.queries = 0
        self.cancels = 0
        self.signals: list[WorkflowSignal] = []
        self.updates: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        self.starts += 1
        self.events.append(
            {
                "event_id": "event-started",
                "event_type": "workflow_started",
                "workflow_id": request.workflow_id,
            }
        )
        return self._status(request.workflow_id, "running")

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self.queries += 1
        return self._status(workflow_id, "running")

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        self.cancels += 1
        return {**self._status(workflow_id, "cancelled"), "reason": reason}

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        self.signals.append(signal)
        return self._status(workflow_id, "signal_sent")

    def update_workflow(self, workflow_id: str, command: dict[str, Any]) -> dict[str, Any]:
        self.updates.append(dict(command))
        return self._status(workflow_id, "command_applied")

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["workflow_id"] == workflow_id]

    def _status(self, workflow_id: str, status: str) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": workflow_id,
            "status": status,
            "events": list(self.events),
        }


class RecordingReleaseAdmission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **values: Any) -> tuple[bool, str]:
        self.calls.append(values)
        return True, "runtime_release_gate_verified"


def _request(
    workflow_id: str = "workflow-control-composition",
    *,
    max_attempts: int = 1,
) -> WorkflowRequest:
    return WorkflowRequest(
        workflow_id=workflow_id,
        steps=(
            WorkflowStepRequest(
                step_id="step-1",
                task_kind="coding",
                policy_scope={"source": "composition-test"},
            ),
        ),
        policy_scope={"source": "composition-test"},
        metadata={"execution_budget": {"max_attempts": max_attempts}},
    )


def _bound_facade(backend: RecordingBackend):
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    facade = build_workflow_backend_control_facade(backend, ownership=ownership)
    return facade, facade.bind(principal), ownership


def test_legacy_backend_shape_is_dispatched_only_through_bound_hub_control() -> None:
    backend = RecordingBackend()
    _facade, bound, _ownership = _bound_facade(backend)

    assert bound.start_workflow(_request())["status"] == "running"
    assert bound.get_workflow_status("workflow-control-composition")["status"] == "running"
    assert (
        bound.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="resume", payload={"revision": 2}, actor="spoofed"),
        )["status"]
        == "signal_sent"
    )
    assert bound.list_workflow_events("workflow-control-composition")[0]["event_id"] == "event-started"
    assert bound.cancel_workflow("workflow-control-composition", "operator request")["status"] == "cancelled"

    assert backend.starts == 1
    # Query reads the Hub-persisted authoritative projection; observing an
    # infrastructure runtime is exclusively a reconciler mutation.
    assert backend.queries == 0
    assert backend.cancels == 1
    assert backend.signals[0].name == "resume"
    assert backend.signals[0].actor == "owner-a"


def test_bound_control_rechecks_tenant_and_subject_before_backend_access() -> None:
    backend = RecordingBackend()
    facade, owner, _ownership = _bound_facade(backend)
    owner.start_workflow(_request())
    foreign = facade.bind(WorkflowRoutePrincipal("tenant-b", "owner-a"))

    with pytest.raises(PermissionError, match="workflow_run_not_found"):
        foreign.get_workflow_status("workflow-control-composition")
    with pytest.raises(PermissionError, match="tenant_binding_mismatch"):
        foreign.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="retry"),
        )

    assert backend.queries == 0
    assert backend.signals == []


def test_temporal_compatibility_path_fails_closed_without_release_admission() -> None:
    backend = RecordingBackend("temporal")
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    bound = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=UnavailableWorkflowRuntimeReleaseAdmission(),
    ).bind(principal)

    with pytest.raises(RuntimeError, match="workflow_runtime_selection_blocked"):
        bound.start_workflow(_request())

    assert backend.starts == 0


def test_local_status_backend_does_not_claim_requested_execution_capabilities() -> None:
    backend = RecordingBackend()
    _facade, bound, _ownership = _bound_facade(backend)
    request = WorkflowRequest(
        workflow_id="workflow-control-composition",
        policy_scope={"source": "composition-test"},
        steps=(
            WorkflowStepRequest(
                step_id="retrieve",
                policy_scope={"source": "composition-test"},
                metadata={"required_capabilities": ["retrieval"]},
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="workflow_runtime_incompatible:retrieval"):
        bound.start_workflow(request)

    assert backend.starts == 0


def test_temporal_admission_receives_legacy_plan_capabilities_and_governance_requirements() -> None:
    backend = RecordingBackend("temporal")
    admission = RecordingReleaseAdmission()
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    bound = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=admission,
    ).bind(principal)
    request = WorkflowRequest(
        workflow_id="workflow-control-composition",
        policy_scope={"source": "composition-test"},
        steps=(
            WorkflowStepRequest(
                step_id="retrieve",
                policy_scope={"source": "composition-test"},
                metadata={"required_capabilities": ["retrieval"]},
            ),
        ),
    )

    assert bound.start_workflow(request)["status"] == "running"

    required = admission.calls[0]["required_capabilities"]
    assert {"audit", "authorization", "policy", "retrieval", "side_effect_guard"} <= required
    assert admission.calls[0]["runtime_id"] == "temporal"


def test_temporal_control_is_transported_as_hub_signed_revision_bound_update() -> None:
    backend = RecordingBackend("temporal")
    admission = RecordingReleaseAdmission()
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    bound = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=admission,
    ).bind(principal)
    bound.start_workflow(_request())

    result = bound.signal_workflow(
        "workflow-control-composition",
        WorkflowSignal(name="pause", payload={}),
    )

    assert backend.signals == []
    assert result["status"] == "command_applied"
    assert backend.updates[0]["schema"] == "ananta.workflow_command.v2"
    assert backend.updates[0]["command_type"] == "pause"
    assert backend.updates[0]["expected_revision"] == 0
    assert backend.updates[0]["signature"]


def test_durable_adapter_itself_requires_hub_verified_command_port() -> None:
    backend = RecordingBackend("temporal")
    durable = WorkflowBackendDurableRunAdapter(backend)

    with pytest.raises(PermissionError, match="temporal_hub_verified_command_required"):
        durable.signal(
            tenant_id="tenant-a",
            run_id="workflow-control-composition",
            command={
                "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                "signal": {"name": "resume", "payload": {}},
            },
        )

    assert backend.signals == []


def test_process_composition_is_singleton_and_hub_files_have_no_worker_imports(
    monkeypatch: pytest.MonkeyPatch,
    workflow_runtime_auth_keyring_file,
) -> None:
    del workflow_runtime_auth_keyring_file
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    reset_workflow_backend_control_facade()

    assert get_workflow_backend_control_facade() is get_workflow_backend_control_facade()

    for relative_path in (
        "agent/services/workflow_control_composition.py",
        "agent/services/chat_process_binding.py",
        "agent/routes/visual_process.py",
        "agent/routes/workflow_runtime_operations.py",
    ):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        modules.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        assert not any(module == "worker" or module.startswith("worker.") for module in modules)
        if relative_path == "agent/services/chat_process_binding.py":
            assert "agent.services.workflow_backend_factory" not in modules


def test_visual_routes_expose_resume_and_retry_through_hub_control(
    monkeypatch: pytest.MonkeyPatch,
    workflow_runtime_auth_keyring_file,
) -> None:
    del workflow_runtime_auth_keyring_file
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    # This route contract test exercises resume/retry, not rollout admission.
    # Rollout itself is covered with mandatory scopes and policies separately.
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_rollout_policies",
        lambda: None,
    )
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_release_admission",
        lambda _backend: RecordingReleaseAdmission(),
    )
    reset_workflow_backend_control_facade()
    workflow_route_authorization_service.clear()
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    client = app.test_client()
    token = generate_token(
        {"sub": "route-owner", "tenant_id": "route-tenant", "role": "user"},
        settings.secret_key,
    )
    headers = {"Authorization": f"Bearer {token}"}
    workflow_id = "workflow-control-route-resume-retry"

    started = client.post(
        "/api/visual-process/workflow/start",
        headers=headers,
        json={
            "workflow_request": _request(
                workflow_id,
                max_attempts=2,
            ).to_dict()
        },
    )
    paused = client.post(
        f"/api/visual-process/workflow/{workflow_id}/signal",
        headers=headers,
        json={"name": "pause", "payload": {}},
    )
    resumed = client.post(
        f"/api/visual-process/workflow/{workflow_id}/resume",
        headers=headers,
    )
    cancelled = client.post(
        f"/api/visual-process/workflow/{workflow_id}/cancel",
        headers=headers,
    )
    retried = client.post(
        f"/api/visual-process/workflow/{workflow_id}/retry",
        headers=headers,
        json={"payload": {"reason": "operator retry"}},
    )

    assert started.status_code == 200, started.get_json()
    assert paused.get_json()["status"] == "paused"
    assert resumed.get_json()["status"] == "running"
    assert cancelled.get_json()["status"] == "cancelled"
    assert retried.get_json()["status"] == "running"
    assert any(
        event["event_type"] == "workflow.run.retry_requested"
        for event in retried.get_json()["events"]
    )
