from __future__ import annotations

import ast
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
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
from agent.services.workflow_control_bindings import InMemoryWorkflowControlBindingStore
from agent.services.workflow_control_command_receipt_persistence import (
    InMemoryWorkflowControlCommandReceiptStore,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandRejectedError,
)
from agent.services.workflow_control_composition import (
    DURABLE_RUN_SIGNAL_SCHEMA,
    UnavailableWorkflowRuntimeReleaseAdmission,
    WorkflowBackendDurableRunAdapter,
    build_workflow_backend_control_facade,
    get_workflow_backend_control_facade,
    reset_workflow_backend_control_facade,
)
from agent.services.workflow_control_dispatch_persistence import (
    InMemoryWorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_production_composition import (
    production_command_key_ring,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)
from agent.services.workflow_runtime.security import HmacKeyRing
from ananta_contracts.temporal_workflow import COMMAND_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


class RecordingBackend:
    def __init__(self, backend_id: str = "local", *, start_status: str = "running") -> None:
        self.backend_id = backend_id
        self.start_status = start_status
        self.starts = 0
        self.queries = 0
        self.cancels = 0
        self.signals: list[WorkflowSignal] = []
        self.updates: list[dict[str, Any]] = []
        self.update_ids: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.requests: dict[str, WorkflowRequest] = {}
        self.runtime_status: dict[str, str] = {}
        self.revisions: dict[str, int] = {}
        self.fail_describe = False
        self.command_result_override: dict[str, Any] | None = None
        self.operations: list[str] = []

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        self.starts += 1
        self.requests[request.workflow_id] = request
        self.runtime_status[request.workflow_id] = self.start_status
        self.revisions[request.workflow_id] = 0
        self.events.append(
            {
                "schema": "ananta.workflow_backend_event.v1",
                "event_id": "event-started",
                "event_type": "workflow_started",
                "workflow_id": request.workflow_id,
            }
        )
        return self._status(request.workflow_id, self.start_status)

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self.queries += 1
        return self._status(workflow_id, self.runtime_status[workflow_id])

    def query_workflow(self, workflow_id: str, query: str) -> dict[str, Any]:
        assert query == "status"
        self.queries += 1
        self.operations.append("describe")
        if self.fail_describe:
            raise RuntimeError("temporal_describe_unavailable")
        status = self.runtime_status[workflow_id]
        revision = self.revisions[workflow_id]
        request = self.requests[workflow_id]
        terminal = status in {"completed", "failed", "cancelled"}
        step_id = request.steps[0].step_id
        return {
            "schema": "ananta.temporal-workflow-status.v1",
            "workflow_id": workflow_id,
            "run_id": str(request.metadata["run_id"]),
            "status": status,
            "revision": revision,
            "current_step_id": "" if terminal else step_id,
            "completed_step_ids": [step_id] if status == "completed" else [],
            "retry_budget_remaining": 1,
            "checkpoint_ref": f"temporal:{workflow_id}:{revision}",
            "open_gates": [],
            "reason_code": "",
            "parameters": {},
            "plan_hash": str(request.metadata["plan_hash"]),
            "plan_revision": 1,
            "plan_ref": "",
            "active_step_ids": [] if terminal else [step_id],
            "failed_step_ids": [step_id] if status == "failed" else [],
        }

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        self.cancels += 1
        self._advance(workflow_id, "cancelled")
        return {**self._status(workflow_id, "cancelled"), "reason": reason}

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        self.signals.append(signal)
        status = {"pause": "paused", "resume": "running", "retry": "running"}.get(
            signal.name,
            "running",
        )
        self._advance(workflow_id, status)
        return self._status(workflow_id, status)

    def update_workflow(
        self,
        workflow_id: str,
        command: dict[str, Any],
        *,
        update_id: str = "",
    ) -> dict[str, Any]:
        self.updates.append(dict(command))
        self.update_ids.append(str(update_id))
        self.operations.append("update")
        status = {
            "pause": "paused",
            "resume": "running",
            "retry": "running",
            "cancel": "cancelled",
        }[str(command["command_type"])]
        self._advance(workflow_id, status)
        result = {
            "schema": "ananta.temporal-workflow-command-result.v2",
            "command_id": command["command_id"],
            "accepted": True,
            "revision": self.revisions[workflow_id],
            "status": status,
            "reason_code": "",
        }
        if self.command_result_override:
            result.update(self.command_result_override)
        return result

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["workflow_id"] == workflow_id]

    def _status(self, workflow_id: str, status: str) -> dict[str, Any]:
        request = self.requests[workflow_id]
        revision = self.revisions[workflow_id]
        terminal = status in {"completed", "failed", "cancelled"}
        step_status = status if terminal else status
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": workflow_id,
            "run_id": str(request.metadata["run_id"]),
            "tenant_id": str(request.metadata["tenant_id"]),
            "plan_hash": str(request.metadata["plan_hash"]),
            "status": status,
            "revision": revision,
            "checkpoint_ref": f"{self.backend_id}:{workflow_id}:{revision}",
            "steps": [
                {
                    "step_id": step.step_id,
                    "status": step_status,
                }
                for step in request.steps
            ],
            "events": list(self.events),
        }

    def _advance(self, workflow_id: str, status: str) -> None:
        self.revisions[workflow_id] += 1
        self.runtime_status[workflow_id] = status


class RecordingReleaseAdmission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **values: Any) -> tuple[bool, str]:
        self.calls.append(values)
        return True, "runtime_release_gate_verified"


class _NoOpReadModelProjector:
    def project(self, **_values: Any) -> None:
        return None


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


def _temporal_facade(
    backend: RecordingBackend,
    *,
    bindings: InMemoryWorkflowControlBindingStore | None = None,
    dispatch_intents=None,
):
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=RecordingReleaseAdmission(),
        bindings=bindings,
        dispatch_intents=dispatch_intents,
        read_model_projector=_NoOpReadModelProjector(),
    )
    return facade, facade.bind(principal), principal


class _RecordingDispatchStore(InMemoryWorkflowControlDispatchIntentStore):
    def __init__(
        self,
        bindings: InMemoryWorkflowControlBindingStore,
        operations: list[str],
    ) -> None:
        super().__init__(bindings)
        self.operations = operations
        self.fail_stage = False

    def stage_command(
        self,
        *,
        binding,
        command,
    ):
        self.operations.append("stage")
        if self.fail_stage:
            raise RuntimeError("dispatch_persistence_unavailable")
        return super().stage_command(
            binding=binding,
            command=command,
        )

    def acknowledge(self, *args, **kwargs):
        self.operations.append("acknowledge")
        return super().acknowledge(*args, **kwargs)


class _FailOnceCommandReceiptCompletion(InMemoryWorkflowControlCommandReceiptStore):
    def __init__(self, bindings: InMemoryWorkflowControlBindingStore) -> None:
        super().__init__(bindings)
        self.failures = 1

    def complete(
        self,
        command_id: str,
        *,
        status: dict[str, Any],
        owner_id: str,
    ):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("command_receipt_completion_lost")
        return super().complete(command_id, status=status, owner_id=owner_id)


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
        == "running"
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


def test_production_multi_runtime_key_ring_is_stable_when_langgraph_is_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable = HmacKeyRing({"stable-control": "x" * 32}, active_key_id="stable-control")
    monkeypatch.setattr(
        "agent.services.workflow_hub_task_gateway_runtime.get_workflow_authorization_key_ring",
        lambda: stable,
    )

    assert production_command_key_ring(RecordingBackend("langgraph")) is stable


def test_bound_control_rechecks_tenant_and_subject_before_backend_access() -> None:
    backend = RecordingBackend()
    facade, owner, _ownership = _bound_facade(backend)
    owner.start_workflow(_request())
    foreign = facade.bind(WorkflowRoutePrincipal("tenant-b", "owner-a"))

    with pytest.raises(PermissionError, match="workflow_run_not_found"):
        foreign.get_workflow_status("workflow-control-composition")
    with pytest.raises(WorkflowControlCommandRejectedError) as rejected:
        foreign.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="retry"),
        )
    assert rejected.value.reason_code == "workflow_tenant_binding_mismatch"

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
        read_model_projector=_NoOpReadModelProjector(),
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
        read_model_projector=_NoOpReadModelProjector(),
    ).bind(principal)
    bound.start_workflow(_request())

    result = bound.signal_workflow(
        "workflow-control-composition",
        WorkflowSignal(name="pause", payload={}),
    )

    assert backend.signals == []
    assert result["status"] == "paused"
    assert backend.updates[0]["schema"] == COMMAND_SCHEMA
    assert backend.updates[0]["command_type"] == "pause"
    assert backend.updates[0]["expected_revision"] == 0
    assert backend.updates[0]["signature"]


def test_temporal_cancel_ack_is_followed_by_exact_authoritative_describe() -> None:
    backend = RecordingBackend("temporal")
    _facade, bound, _principal = _temporal_facade(backend)
    bound.start_workflow(_request())

    result = bound.cancel_workflow(
        "workflow-control-composition",
        "operator request",
    )

    assert result["status"] == "cancelled"
    assert result["source_observation"] == {
        "schema": "ananta.temporal-workflow-status.v1",
        "status": "cancelled",
        "revision": 1,
    }
    assert result["steps"] == [{"step_id": "step-1", "status": "cancelled"}]
    assert backend.updates[0]["command_type"] == "cancel"
    assert backend.queries == 2


def test_temporal_dispatch_persists_pending_before_external_mutation() -> None:
    operations: list[str] = []
    bindings = InMemoryWorkflowControlBindingStore()
    intents = _RecordingDispatchStore(bindings, operations)
    backend = RecordingBackend("temporal")
    backend.operations = operations
    _facade, bound, _principal = _temporal_facade(
        backend,
        bindings=bindings,
        dispatch_intents=intents,
    )
    bound.start_workflow(_request())
    operations.clear()

    result = bound.signal_workflow(
        "workflow-control-composition",
        WorkflowSignal(name="pause"),
    )

    assert result["status"] == "paused"
    assert operations == ["stage", "update", "acknowledge", "describe"]


def test_pending_persistence_failure_prevents_temporal_dispatch() -> None:
    operations: list[str] = []
    bindings = InMemoryWorkflowControlBindingStore()
    intents = _RecordingDispatchStore(bindings, operations)
    backend = RecordingBackend("temporal")
    backend.operations = operations
    _facade, bound, _principal = _temporal_facade(
        backend,
        bindings=bindings,
        dispatch_intents=intents,
    )
    bound.start_workflow(_request())
    operations.clear()
    intents.fail_stage = True

    with pytest.raises(RuntimeError, match="dispatch_persistence_unavailable"):
        bound.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="pause"),
        )

    assert operations == ["stage"]
    assert backend.updates == []


def test_describe_failure_keeps_command_pending_until_reconciler_materializes_status() -> None:
    store = InMemoryWorkflowControlBindingStore()
    backend = RecordingBackend("temporal")
    facade, bound, _principal = _temporal_facade(backend, bindings=store)
    bound.start_workflow(_request())
    backend.fail_describe = True

    with pytest.raises(
        RuntimeError,
        match="workflow_control_command_observation_pending",
    ):
        bound.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="pause"),
        )
    assert len(backend.updates) == 1

    with pytest.raises(RuntimeError, match="workflow_control_dispatch_active_conflict"):
        bound.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="pause"),
        )
    assert len(backend.updates) == 1

    backend.fail_describe = False
    time.sleep(1.05)
    reconciled = facade.registry.reconcile_active()
    assert reconciled["processed"] >= 1
    assert reconciled["failed"] == []
    stored = store.last_status("workflow-control-composition")
    assert stored is not None
    assert stored["status"] == "paused"
    assert stored["source_observation"] == {
        "schema": "ananta.temporal-workflow-status.v1",
        "status": "paused",
        "revision": 1,
    }
    assert len(backend.updates) == 1


def test_malformed_temporal_ack_is_never_relabelled_as_runtime_status() -> None:
    backend = RecordingBackend("temporal")
    backend.command_result_override = {"command_id": "foreign-command"}
    facade, bound, _principal = _temporal_facade(backend)
    bound.start_workflow(_request())

    with pytest.raises(
        RuntimeError,
        match="workflow_control_command_observation_pending",
    ):
        bound.signal_workflow(
            "workflow-control-composition",
            WorkflowSignal(name="pause"),
        )

    assert len(backend.updates) == 1
    assert backend.queries == 1
    assert facade.bindings.last_status("workflow-control-composition")["status"] == "running"


@pytest.mark.parametrize("runtime_id", ["local", "langgraph", "temporal"])
def test_runtime_command_uses_current_step_and_rejects_plan_edit_without_receipt(
    runtime_id: str,
) -> None:
    workflow_id = f"workflow-command-step-{runtime_id}"
    backend = RecordingBackend(runtime_id)
    bindings = InMemoryWorkflowControlBindingStore()
    receipts = InMemoryWorkflowControlCommandReceiptStore(bindings)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve(workflow_id, principal) == "reserved"
    facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        bindings=bindings,
        command_receipts=receipts,
        release_admission=RecordingReleaseAdmission(),
        read_model_projector=_NoOpReadModelProjector(),
    )
    bound = facade.bind(principal)
    bound.start_workflow(_request(workflow_id))

    with pytest.raises(
        WorkflowControlCommandRejectedError,
        match="workflow_control_command_rejected",
    ) as rejected:
        bound.command_workflow(
            workflow_id,
            command_type="edit",
            command_id=f"{runtime_id}-edit-rejected",
            payload={
                "replacement_plan": {"nodes": []},
                "replacement_plan_hash": "b" * 64,
            },
        )
    paused = bound.command_workflow(
        workflow_id,
        command_type="pause",
        command_id=f"{runtime_id}-pause-current-step",
    )

    assert rejected.value.reason_code == "workflow_plan_edit_rebind_required"
    assert receipts.get(f"{runtime_id}-edit-rejected") is None
    assert paused["status"] == "paused"
    if runtime_id == "temporal":
        assert len(backend.updates) == 1
        assert backend.updates[0]["step_id"] == "step-1"
        assert backend.signals == []
    else:
        assert backend.updates == []
        assert len(backend.signals) == 1


@pytest.mark.parametrize("runtime_id", ["local", "langgraph", "temporal"])
def test_divergent_replay_of_command_id_is_typed_conflict_without_second_mutation(
    runtime_id: str,
) -> None:
    workflow_id = f"workflow-command-conflict-{runtime_id}"
    backend = RecordingBackend(runtime_id)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve(workflow_id, principal) == "reserved"
    bound = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=RecordingReleaseAdmission(),
        read_model_projector=_NoOpReadModelProjector(),
    ).bind(principal)
    bound.start_workflow(_request(workflow_id))
    first = bound.command_workflow(
        workflow_id,
        command_type="pause",
        command_id="same-command-id",
    )

    with pytest.raises(WorkflowControlCommandRejectedError) as conflict:
        bound.command_workflow(
            workflow_id,
            command_type="resume",
            command_id="same-command-id",
        )

    assert first["status"] == "paused"
    assert conflict.value.reason_code == "workflow_control_command_id_conflict"
    assert len(backend.updates if runtime_id == "temporal" else backend.signals) == 1


def test_terminal_failed_start_retains_binding_and_queryable_status() -> None:
    backend = RecordingBackend(start_status="failed")
    facade, bound, _ownership = _bound_facade(backend)

    started = bound.start_workflow(_request())

    assert started["status"] == "failed"
    assert facade.bindings.get("workflow-control-composition") is not None
    assert bound.get_workflow_status("workflow-control-composition")["status"] == "failed"


@pytest.mark.parametrize("runtime_id", ["local", "langgraph"])
def test_stable_command_id_recovers_lost_response_once_across_sync_runtimes(
    runtime_id: str,
) -> None:
    bindings = InMemoryWorkflowControlBindingStore()
    receipts = _FailOnceCommandReceiptCompletion(bindings)
    backend = RecordingBackend(runtime_id)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        bindings=bindings,
        command_receipts=receipts,
        release_admission=RecordingReleaseAdmission(),
    )
    bound = facade.bind(principal)
    bound.start_workflow(_request())

    with pytest.raises(RuntimeError, match="command_receipt_completion_lost"):
        bound.command_workflow(
            "workflow-control-composition",
            command_type="pause",
            command_id="client-command-once",
        )
    recovered = bound.command_workflow(
        "workflow-control-composition",
        command_type="pause",
        command_id="client-command-once",
    )
    repeated = bound.command_workflow(
        "workflow-control-composition",
        command_type="pause",
        command_id="client-command-once",
    )

    assert recovered["status"] == "paused"
    assert repeated == recovered
    assert len(backend.signals) == 1
    assert receipts.get("client-command-once").state == "completed"


@pytest.mark.parametrize("runtime_id", ["local", "langgraph"])
def test_pending_sync_receipt_is_recovered_by_hub_drain_without_client_retry(
    runtime_id: str,
) -> None:
    bindings = InMemoryWorkflowControlBindingStore()
    receipts = _FailOnceCommandReceiptCompletion(bindings)
    backend = RecordingBackend(runtime_id)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        bindings=bindings,
        command_receipts=receipts,
        release_admission=RecordingReleaseAdmission(),
    )
    bound = facade.bind(principal)
    bound.start_workflow(_request())

    with pytest.raises(RuntimeError, match="command_receipt_completion_lost"):
        bound.command_workflow(
            "workflow-control-composition",
            command_type="pause",
            command_id="client-command-background",
        )

    report = facade.reconcile_active()

    assert report["processed"] >= 1
    assert receipts.get("client-command-background").state == "completed"
    assert len(backend.signals) == 1
    resumed = bound.command_workflow(
        "workflow-control-composition",
        command_type="resume",
        command_id="client-command-after-recovery",
    )
    assert resumed["status"] == "running"
    assert len(backend.signals) == 2


def test_parallel_sync_receipt_recovery_is_serialized_across_facades_and_drain() -> None:
    signal_entered = Event()
    release_signal = Event()
    competing_claim_blocked = Event()

    class BlockingBackend(RecordingBackend):
        def signal_workflow(
            self,
            workflow_id: str,
            signal: WorkflowSignal,
        ) -> dict[str, Any]:
            signal_entered.set()
            assert release_signal.wait(timeout=5)
            return super().signal_workflow(workflow_id, signal)

    class RecordingReceiptStore(InMemoryWorkflowControlCommandReceiptStore):
        def claim(self, *args, **kwargs):
            claimed = super().claim(*args, **kwargs)
            if claimed is None:
                competing_claim_blocked.set()
            return claimed

    backend = BlockingBackend("langgraph")
    bindings = InMemoryWorkflowControlBindingStore()
    receipts = RecordingReceiptStore(bindings)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-control-composition", principal) == "reserved"
    first_facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        bindings=bindings,
        command_receipts=receipts,
        release_admission=RecordingReleaseAdmission(),
    )
    first = first_facade.bind(principal)
    first.start_workflow(_request())
    second_facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        bindings=bindings,
        command_receipts=receipts,
        release_admission=RecordingReleaseAdmission(),
    )
    second = second_facade.bind(principal)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(
            first.command_workflow,
            "workflow-control-composition",
            command_type="pause",
            command_id="parallel-command-once",
        )
        assert signal_entered.wait(timeout=5)
        second_result = pool.submit(
            second.command_workflow,
            "workflow-control-composition",
            command_type="pause",
            command_id="parallel-command-once",
        )
        assert competing_claim_blocked.wait(timeout=5)
        drain_report = second_facade.reconcile_active()
        release_signal.set()
        first_status = first_result.result(timeout=5)
        second_status = second_result.result(timeout=5)

    assert first_status == second_status
    assert first_status["status"] == "paused"
    assert len(backend.signals) == 1
    assert receipts.get("parallel-command-once").state == "completed"
    assert drain_report["failed"] == []


def test_repeated_public_status_read_is_timestamp_and_payload_stable() -> None:
    backend = RecordingBackend("local")
    _facade, bound, _ownership = _bound_facade(backend)
    started = bound.start_workflow(_request())

    first = bound.get_workflow_status("workflow-control-composition")
    second = bound.get_workflow_status("workflow-control-composition")

    assert first == second == started


def test_parallel_exact_temporal_start_loser_never_discards_winner_binding() -> None:
    entered = Event()
    release = Event()

    class BlockingStartBackend(RecordingBackend):
        def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
            entered.set()
            assert release.wait(timeout=5)
            return super().start_workflow(request)

    backend = BlockingStartBackend("temporal")
    facade, bound, _principal = _temporal_facade(backend)
    request = _request()

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(
            bound.start_workflow,
            request,
            command_id="client-start-race",
        )
        assert entered.wait(timeout=5)
        loser = pool.submit(
            bound.start_workflow,
            request,
            command_id="client-start-race",
        )
        with pytest.raises(
            RuntimeError,
            match="workflow_control_start_observation_pending",
        ):
            loser.result(timeout=5)
        release.set()
        assert winner.result(timeout=5)["status"] == "running"

    assert backend.starts == 1
    assert facade.bindings.get(request.workflow_id) is not None
    assert facade.bindings.last_status(request.workflow_id)["status"] == "running"


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
    events = client.get(
        f"/api/visual-process/workflow/{workflow_id}/events",
        headers=headers,
    )
    assert events.status_code == 200
    assert any(event["event_type"] == "workflow.run.retry_requested" for event in events.get_json()["events"])


def test_cancel_route_returns_stable_nonretryable_conflict_for_typed_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_route_authorization_service.clear()
    principal = WorkflowRoutePrincipal("route-tenant", "route-owner")
    workflow_id = "workflow-cancel-rejected"
    assert workflow_route_authorization_service.reserve(workflow_id, principal) == "reserved"

    class Backend:
        calls = 0

        def command_workflow(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict")
            return {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": "ananta-native",
                "workflow_id": workflow_id,
                "status": "cancelled",
            }

    backend = Backend()
    monkeypatch.setattr(
        "agent.routes.visual_process.configured_workflow_backend",
        lambda _principal: (backend, None),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    token = generate_token(
        {"sub": "route-owner", "tenant_id": "route-tenant", "role": "user"},
        settings.secret_key,
    )
    headers = {"Authorization": f"Bearer {token}"}

    rejected = app.test_client().post(
        f"/api/visual-process/workflow/{workflow_id}/cancel",
        headers=headers,
        json={"command_id": "cancel-rejected"},
    )
    accepted = app.test_client().post(
        f"/api/visual-process/workflow/{workflow_id}/cancel",
        headers=headers,
        json={"command_id": "cancel-after-rejection"},
    )

    assert rejected.status_code == 409
    assert rejected.get_json()["data"]["reason_code"] == "workflow_control_command_id_conflict"
    assert rejected.get_json()["data"]["retryable"] is False
    assert accepted.status_code == 200
    assert accepted.get_json()["status"] == "cancelled"
    assert backend.calls == 2
