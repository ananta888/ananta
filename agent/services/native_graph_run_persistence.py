"""Canonical event emission, terminal handling, BPMN event reconciliation, and checkpoint
persistence for Native graph runs.

``NativeGraphEventEmitter`` and ``NativeGraphRunLifecycle`` are collaborators
composed by ``NativeGraphOrchestrator``; each receives only the stores and
ports it uses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.services.bpmn_event_wait_contracts import EventWaitError
from agent.services.bpmn_native_event_runtime import BpmnNativeEventRuntime
from agent.services.native_graph_checkpoint_service import NativeGraphCheckpointService
from agent.services.native_graph_delegation_service import NativeGraphDelegationService
from agent.services.native_graph_event_appender import append_native_event
from agent.services.native_graph_models import (
    NATIVE_GRAPH_RUNTIME_ID,
    NATIVE_GRAPH_RUNTIME_VERSION,
    NativeGraphEventEmitterPort,
    NativeGraphRequest,
    NativeGraphResult,
    NativeRunState,
    safe_native_reason_code,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeResult
from agent.services.workflow_runtime.security import SignedCheckpoint
from agent.services.workflow_runtime.side_effects import SideEffectLedger, side_effect_event


class NativeGraphEventEmitter:
    """Append canonical Native run events and reconciled side-effect observations."""

    def __init__(self, *, events: EventStore, ledger: SideEffectLedger, clock: Callable[[], float]) -> None:
        self._events = events
        self._ledger = ledger
        self._clock = clock

    def emit_side_effect_if_present(
        self,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        result: NativeNodeResult,
        running: dict[str, Any],
    ) -> None:
        operation_id = str(running.get("operation_id") or "")
        if not operation_id:
            return
        record = self._ledger.get(tenant_id=plan.tenant_id, operation_id=operation_id)
        if record is None or record.status != result.side_effect_status:
            raise ValueError("native_side_effect_result_not_reconciled")
        event = side_effect_event(
            record,
            correlation_id=request.correlation_id or request.run_id,
            causation_id=result.result_id,
            actor="native-worker",
        )
        stored = append_native_event(
            self._events, event, observed_sequence=state.event_sequence, lease=state.control_lease
        )
        state.event_sequence = max(state.event_sequence, stored.sequence)

    def emit(
        self,
        state: NativeRunState,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        event_type: str,
        dedupe_key: str,
        step_id: str = "",
        attempt: int = 0,
        actor: str = "hub",
        payload: dict[str, Any] | None = None,
    ) -> CanonicalWorkflowEvent:
        if state.control_lease is not None:
            state.control_lease.ensure_valid()
        event = CanonicalWorkflowEvent.build(
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            step_id=step_id,
            attempt=attempt,
            event_type=event_type,
            actor=actor,
            correlation_id=request.correlation_id or request.run_id,
            causation_id=request.control_task_id,
            dedupe_key=dedupe_key,
            payload={
                **dict(payload or {}),
                "runtime_observation": {
                    "task_id": request.control_task_id,
                    "runtime": NATIVE_GRAPH_RUNTIME_ID,
                    "mode": "live",
                    "capabilities": list(plan.capabilities),
                    "stale_after_seconds": 300.0,
                    "degraded": False,
                },
            },
            occurred_at=float(self._clock()),
        )
        stored = append_native_event(
            self._events, event, observed_sequence=state.event_sequence, lease=state.control_lease
        )
        state.event_sequence = max(state.event_sequence, stored.sequence)
        return stored


class NativeGraphRunLifecycle:
    """Finish, fail, and cancel runs; reconcile BPMN events; persist signed checkpoints."""

    def __init__(
        self,
        *,
        emitter: NativeGraphEventEmitterPort,
        events: EventStore,
        checkpoints: NativeGraphCheckpointService,
        bpmn_events: BpmnNativeEventRuntime,
        delegation: NativeGraphDelegationService,
        node_input: Callable[[ExecutionNode, NativeRunState], dict[str, Any]],
    ) -> None:
        self._emitter = emitter
        self._events = events
        self._checkpoints = checkpoints
        self._bpmn_events = bpmn_events
        self._delegation = delegation
        self._node_input = node_input

    def finish_if_terminal(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if state.status != "running" or state.running or state.open_gates:
            return
        terminal = state.completed | state.skipped | set(state.failed)
        if len(terminal) != len(plan.nodes):
            return
        required = {artifact.artifact_id for artifact in plan.artifacts if artifact.required}
        missing = required - set(state.artifact_refs)
        if missing:
            self.fail_run(plan, request, state, f"native_required_artifacts_missing:{','.join(sorted(missing))}")
            return
        state.status = "completed"
        self._emitter.emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.completed",
            dedupe_key=f"native:{request.run_id}:completed",
            payload={"artifact_ids": sorted(state.artifact_refs)},
        )

    def fail_run(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, reason: str) -> None:
        self.cancel_running(plan, request, state, reason)
        state.status = "failed"
        state.reason_code = safe_native_reason_code(reason)
        self._emitter.emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.failed",
            dedupe_key=f"native:{request.run_id}:failed:{state.event_sequence + 1}",
            payload={"reason_code": state.reason_code},
        )

    def cancel_running(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, reason: str
    ) -> None:
        if any(node.node_type == "bpmn_wait" for node in plan.nodes):
            self._bpmn_events.cancel_run(**self.bpmn_event_hooks(plan, request, state))
        self._delegation.cancel_running(
            plan=plan,
            request=request,
            state=state,
            reason=reason,
            input_for=self._node_input,
            emit=self._emitter.emit,
        )

    def bpmn_event_hooks(self, plan, request, state):
        if state.control_lease is None:
            raise EventWaitError("bpmn_wait_run_lease_required")
        return dict(
            plan=plan,
            request=request,
            state=state,
            fencing_token=state.control_lease.fencing_token,
            guard=state.control_lease.ensure_valid,
            persist=lambda: self.save_checkpoint(plan, request, state),
        )

    def reconcile_bpmn_events(self, plan, request, state):
        if not state.bpmn_waits:
            return
        try:
            receipts = self._bpmn_events.reconcile(**self.bpmn_event_hooks(plan, request, state))
        except EventWaitError as exc:
            self.fail_run(plan, request, state, str(exc))
            return
        for receipt in receipts:
            state.control_lease.ensure_valid()
            stored = append_native_event(
                self._events, receipt.to_event(), observed_sequence=state.event_sequence, lease=state.control_lease
            )
            state.event_sequence = max(state.event_sequence, stored.sequence)

    def save_checkpoint(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState
    ) -> SignedCheckpoint:
        return self._checkpoints.save(
            plan=plan,
            request=request,
            state=state,
            emit=self._emitter.emit,
        )

    def load_verified(
        self,
        requested_plan: ExecutionPlan,
        request: NativeGraphRequest,
    ) -> tuple[SignedCheckpoint, NativeRunState, ExecutionPlan]:
        return self._checkpoints.load_verified(
            requested_plan=requested_plan,
            request=request,
        )

    def effective_plan(
        self,
        requested_plan: ExecutionPlan,
        state: NativeRunState,
        checkpoint: SignedCheckpoint,
    ) -> ExecutionPlan:
        return self._checkpoints.effective_plan(
            requested_plan=requested_plan,
            state=state,
            checkpoint=checkpoint,
        )

    def verify_checkpoint(
        self, checkpoint: SignedCheckpoint, plan: ExecutionPlan, request: NativeGraphRequest
    ) -> None:
        self._checkpoints.verify(
            checkpoint=checkpoint,
            plan=plan,
            request=request,
        )


def native_graph_result(
    plan: ExecutionPlan,
    request: NativeGraphRequest,
    state: NativeRunState,
    checkpoint: SignedCheckpoint,
) -> NativeGraphResult:
    return NativeGraphResult(
        runtime_id=NATIVE_GRAPH_RUNTIME_ID,
        runtime_version=NATIVE_GRAPH_RUNTIME_VERSION,
        tenant_id=plan.tenant_id,
        workflow_id=plan.workflow_id,
        run_id=request.run_id,
        control_task_id=request.control_task_id,
        status=state.status,
        checkpoint=checkpoint,
        event_cursor=state.event_sequence,
        completed_node_ids=tuple(sorted(state.completed)),
        failed_nodes=dict(sorted(state.failed.items())),
        open_gates=tuple(sorted(state.open_gates)),
        artifact_refs=dict(sorted(state.artifact_refs.items())),
        reason_code=state.reason_code,
        effective_plan=plan,
    )
