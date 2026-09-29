"""Hub-owned orchestration for the Ananta Native graph runtime.

This service is intentionally a resumable state-machine tick.  It performs only
control-plane work (routing, gates, fan-out selection, deterministic merge,
ownership and persistence).  Every executable task node is submitted through
``HubTaskQueuePort``; the service has no in-process worker fallback.

The orchestrator composes collaborators built in ``__init__``: a node
evaluator, an event emitter, a run lifecycle (termination, BPMN
reconciliation, checkpoints), a tick scheduler and a command applier from the
``native_graph_*`` modules.  Each receives only its own stores and ports; the
evaluator and emitter can be replaced through keyword-only constructor
parameters.
"""

from __future__ import annotations

import hashlib
import math
import time

from agent.services.bpmn_event_wait_store import CheckpointEventWaitStore
from agent.services.bpmn_native_event_runtime import BpmnNativeEventRuntime, event_plan_scope
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_graph_checkpoint_service import NativeGraphCheckpointService
from agent.services.native_graph_command_application import NativeGraphCommandApplier
from agent.services.native_graph_delegation_service import NativeGraphDelegationService
from agent.services.native_graph_models import (
    NATIVE_GRAPH_RUNTIME_ID,
    NATIVE_GRAPH_RUNTIME_VERSION,
    NATIVE_GRAPH_TERMINAL_STATUSES,
    NativeControlPolicyPort,
    NativeGraphEventEmitterPort,
    NativeGraphRequest,
    NativeGraphResult,
    NativeGraphValidation,
    NativeRunState,
    WorkflowPlanArtifactPort,
)
from agent.services.native_graph_node_evaluation import NativeGraphNodeEvaluator
from agent.services.native_graph_run_persistence import (
    NativeGraphEventEmitter,
    NativeGraphRunLifecycle,
    native_graph_result,
)
from agent.services.native_graph_scheduling import NativeGraphScheduler
from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecisionPort,
    build_workflow_provider_decision_service,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.commands import SignedWorkflowCommand, WorkflowCommandVerifier
from agent.services.workflow_runtime.components import (
    WorkflowComponentCompiler,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.native_graph_ports import HubTaskQueuePort
from agent.services.workflow_runtime.ownership import ExecutionOwnershipStore
from agent.services.workflow_runtime.persistence import CheckpointStore
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    SignedCheckpoint,
)
from agent.services.workflow_runtime.side_effects import SideEffectLedger

_TERMINAL = NATIVE_GRAPH_TERMINAL_STATUSES


def _command_fingerprint(command: SignedWorkflowCommand) -> str:
    """Hash stable command semantics, excluding renewable signature fields."""

    value = {
        "schema": command.schema,
        "command_id": command.command_id,
        "command_type": command.command_type,
        "tenant_id": command.tenant_id,
        "workflow_id": command.workflow_id,
        "run_id": command.run_id,
        "step_id": command.step_id,
        "checkpoint_id": command.checkpoint_id,
        "expected_revision": command.expected_revision,
        "plan_hash": command.plan_hash,
        "policy_version": command.policy_version,
        "actor_id": command.actor_id,
        "actor_roles": sorted(command.actor_roles),
        "payload": dict(command.payload),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class NativeGraphOrchestrator:
    """Production Native runtime coordinator owned exclusively by the Hub."""

    runtime_id = NATIVE_GRAPH_RUNTIME_ID
    runtime_version = NATIVE_GRAPH_RUNTIME_VERSION
    capabilities = frozenset(
        {
            "bpmn_control_v1",
            "bpmn_activation_v1",
            "bpmn_events_v1",
            "approval",
            "bounded_parallel",
            "checkpoint",
            "deterministic_merge",
            "resume",
            "stream",
            "subgraphs",
        }
    )

    def __init__(
        self,
        *,
        queue: HubTaskQueuePort,
        checkpoints: CheckpointStore,
        events: EventStore,
        ownership: ExecutionOwnershipStore,
        ledger: SideEffectLedger,
        key_ring: HmacKeyRing,
        command_verifier: WorkflowCommandVerifier,
        policy: NativeControlPolicyPort,
        authorization_grants: WorkflowAuthorizationGrantPort | None = None,
        component_compiler: WorkflowComponentCompiler | None = None,
        plan_artifacts: WorkflowPlanArtifactPort | None = None,
        provider_decisions: WorkflowProviderDecisionPort | None = None,
        bpmn_events: BpmnNativeEventRuntime | None = None,
        clock=time.time,
        node_evaluator: NativeGraphNodeEvaluator | None = None,
        event_emitter: NativeGraphEventEmitterPort | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._events = events
        grants = authorization_grants or InMemoryWorkflowAuthorizationGrantService(clock=clock)
        self._commands = command_verifier
        self._policy = policy
        self._components = component_compiler
        self._clock = clock
        self._run_leases = BpmnRunLeaseService(checkpoints=checkpoints, keys=key_ring, clock=clock)
        bpmn_events = bpmn_events or BpmnNativeEventRuntime(
            store=CheckpointEventWaitStore(checkpoints, key_ring),
            clock=clock,
        )
        delegation = NativeGraphDelegationService(
            queue=queue,
            ownership=ownership,
            ledger=ledger,
            key_ring=key_ring,
            authorization_grants=grants,
            provider_decisions=(provider_decisions or build_workflow_provider_decision_service()),
            clock=clock,
        )
        checkpoint_service = NativeGraphCheckpointService(
            checkpoints=checkpoints,
            key_ring=key_ring,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            clock=clock,
            compile_plan=self._compile,
        )
        evaluator = node_evaluator or NativeGraphNodeEvaluator()
        emitter: NativeGraphEventEmitterPort = event_emitter or NativeGraphEventEmitter(
            events=events, ledger=ledger, clock=clock
        )
        self._emitter = emitter
        self._lifecycle = NativeGraphRunLifecycle(
            emitter=emitter,
            events=events,
            checkpoints=checkpoint_service,
            bpmn_events=bpmn_events,
            delegation=delegation,
            node_input=evaluator.node_input,
        )
        self._scheduler = NativeGraphScheduler(
            queue=queue,
            ownership=ownership,
            policy=policy,
            delegation=delegation,
            bpmn_events=bpmn_events,
            lifecycle=self._lifecycle,
            emitter=emitter,
            evaluator=evaluator,
            clock=clock,
        )
        self._command_applier = NativeGraphCommandApplier(
            emitter=emitter,
            run_control=self._lifecycle,
            bpmn_events=bpmn_events,
            plan_artifacts=plan_artifacts,
            compile_plan=self._compile,
            validate_plan=self.validate,
        )

    def validate(self, plan: ExecutionPlan) -> NativeGraphValidation:
        reasons = [issue.code for issue in plan.validate()]
        if any(node.node_type == "bpmn_wait" for node in plan.nodes):
            try:
                event_plan_scope(plan)
            except ValueError as exc:
                reasons.append(str(exc))
        unsupported = (
            set(plan.capabilities)
            - set(self.capabilities)
            - {
                "retrieval",
                "structured_output",
                "tool_calling",
            }
        )
        reasons.extend(f"native_capability_unsupported:{value}" for value in sorted(unsupported))
        for node in plan.nodes:
            if node.node_type not in {"task", "merge", "checkpoint", "component", "bpmn_control", "bpmn_wait"}:
                reasons.append(f"native_node_type_unsupported:{node.node_type}")
            if (
                node.side_effect_class in {"idempotent_write", "non_idempotent_write"}
                and not str(
                    node.metadata.get("operation_name") or node.metadata.get("declared_operation") or ""
                ).strip()
            ):
                reasons.append(f"native_declared_operation_required:{node.node_id}")
            if node.node_type == "merge":
                if node.metadata.get("merge_strategy") not in {
                    "ordered-by-node-id",
                    "object-by-node-id",
                }:
                    reasons.append(f"native_merge_strategy_invalid:{node.node_id}")
                if node.metadata.get("partial_failure", "fail") not in {"fail", "omit"}:
                    reasons.append(f"native_merge_partial_failure_invalid:{node.node_id}")
        return NativeGraphValidation(not reasons, tuple(sorted(set(reasons))), plan.plan_hash)

    def start(self, request: NativeGraphRequest) -> NativeGraphResult:
        request.assert_valid()
        with self._run_leases.acquire(request) as lease:
            return self._start_owned(request, lease)

    def _start_owned(self, request: NativeGraphRequest, lease) -> NativeGraphResult:
        request.assert_valid()
        plan = self._compile(request.plan)
        validation = self.validate(plan)
        if not validation.valid:
            raise ValueError(f"native_graph_plan_invalid:{','.join(validation.reason_codes)}")
        if (
            self._checkpoints.get_latest(
                tenant_id=plan.tenant_id,
                run_id=request.run_id,
                task_id=request.control_task_id,
            )
            is not None
        ):
            raise ValueError("native_graph_run_already_exists")
        state = NativeRunState(
            input_data=dict(request.input_data),
            tenant_parallel_limit=request.tenant_parallel_limit,
            worker_parallel_limit=request.worker_parallel_limit,
            base_plan_hash=plan.plan_hash,
            effective_plan=plan.to_dict(),
            control_lease=lease,
        )
        if plan.metadata.get("bpmn_definition_hash") or plan.metadata.get("bpmn_activation_expansion"):
            state.bpmn_started_at = float(self._clock())
            if not math.isfinite(state.bpmn_started_at) or state.bpmn_started_at < 0:
                raise ValueError("bpmn_clock_invalid")
            state.bpmn_deadline_at = state.bpmn_started_at + plan.budget.timeout_seconds
            if not math.isfinite(state.bpmn_deadline_at):
                raise ValueError("bpmn_deadline_invalid")
        self._emitter.emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.started",
            dedupe_key=f"native:{request.run_id}:started",
            payload={"runtime_id": self.runtime_id, "plan_hash": plan.plan_hash},
        )
        self._scheduler.tick(plan, request, state)
        checkpoint = self._lifecycle.save_checkpoint(plan, request, state)
        return native_graph_result(plan, request, state, checkpoint)

    def advance(self, request: NativeGraphRequest) -> NativeGraphResult:
        request.assert_valid()
        with self._run_leases.acquire(request) as lease:
            return self._advance_owned(request, lease)

    def _advance_owned(self, request: NativeGraphRequest, lease) -> NativeGraphResult:
        request.assert_valid()
        requested_plan = self._compile(request.plan)
        checkpoint, state, plan = self._lifecycle.load_verified(requested_plan, request)
        state.control_lease = lease
        self._assert_request_state_binding(request, checkpoint, state)
        if state.status in _TERMINAL:
            return native_graph_result(plan, request, state, checkpoint)
        if self._scheduler.expire_bpmn_run(plan, request, state):
            return native_graph_result(plan, request, state, self._lifecycle.save_checkpoint(plan, request, state))
        if state.status != "running":
            return native_graph_result(plan, request, state, checkpoint)
        self._scheduler.tick(plan, request, state)
        updated = self._lifecycle.save_checkpoint(plan, request, state)
        return native_graph_result(plan, request, state, updated)

    def inspect(self, request: NativeGraphRequest) -> NativeGraphResult:
        """Read the latest verified state without ticking or persisting it."""

        request.assert_valid()
        requested_plan = self._compile(request.plan)
        checkpoint, state, plan = self._lifecycle.load_verified(requested_plan, request)
        self._assert_request_state_binding(request, checkpoint, state)
        return native_graph_result(plan, request, state, checkpoint)

    def resume(
        self,
        request: NativeGraphRequest,
        *,
        command: SignedWorkflowCommand,
        checkpoint: SignedCheckpoint | None = None,
        admitted_replay: bool = False,
    ) -> NativeGraphResult:
        request.assert_valid()
        with self._run_leases.acquire(request) as lease:
            return self._resume_owned(
                request, command=command, checkpoint=checkpoint, admitted_replay=admitted_replay, lease=lease
            )

    def _resume_owned(
        self,
        request: NativeGraphRequest,
        *,
        command: SignedWorkflowCommand,
        checkpoint: SignedCheckpoint | None,
        admitted_replay: bool,
        lease,
    ) -> NativeGraphResult:
        request.assert_valid()
        requested_plan = self._compile(request.plan)
        if checkpoint is None:
            current, state, plan = self._lifecycle.load_verified(requested_plan, request)
        else:
            current = checkpoint
            if lease is not None:
                authoritative = self._checkpoints.get_latest(
                    tenant_id=requested_plan.tenant_id, run_id=request.run_id, task_id=request.control_task_id
                )
                if authoritative is None or authoritative.checkpoint_id != current.checkpoint_id:
                    raise ValueError("bpmn_checkpoint_revision_stale")
            state = NativeRunState.from_workflow_state(current.state)
            plan = self._lifecycle.effective_plan(requested_plan, state, current)
            self._lifecycle.verify_checkpoint(current, plan, request)
        state.control_lease = lease
        state.checkpoint_revision = current.revision
        self._assert_request_state_binding(request, current, state)
        command_fingerprint = _command_fingerprint(command)
        if state.last_command_id == command.command_id:
            # A checkpoint is the Native runtime's durable mutation receipt.
            # Exact retries are verified but never re-apply the transition;
            # this closes the checkpoint-save -> Hub-binding-commit window.
            duplicate_verifier = self._commands.verify_persisted if admitted_replay else self._commands.verify
            duplicate_verifier(
                command,
                tenant_id=plan.tenant_id,
                workflow_id=plan.workflow_id,
                run_id=request.run_id,
                step_id=command.step_id,
                checkpoint_id=command.checkpoint_id,
                expected_revision=command.expected_revision,
                plan_hash=plan.plan_hash,
                policy_version=plan.policy_version,
                now=float(self._clock()),
            )
            if state.last_command_fingerprint != command_fingerprint:
                raise PermissionError("native_control_command_receipt_conflict")
            return native_graph_result(plan, request, state, current)
        verifier = self._commands.verify_persisted if admitted_replay else self._commands.verify_once
        verifier(
            command,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            step_id=command.step_id,
            checkpoint_id=current.checkpoint_id,
            expected_revision=current.revision,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            now=float(self._clock()),
        )
        allowed, reason = self._policy.authorize_command(command, plan=plan, state=state)
        if not allowed:
            raise PermissionError(reason or "native_control_policy_denied")
        if not self._scheduler.expire_bpmn_run(plan, request, state):
            plan = self._command_applier.apply(plan, request, state, command)
        if state.status == "running":
            self._scheduler.tick(plan, request, state)
        state.last_command_id = command.command_id
        state.last_command_fingerprint = command_fingerprint
        updated = self._lifecycle.save_checkpoint(plan, request, state)
        return native_graph_result(plan, request, state, updated)

    def checkpoint(self, request: NativeGraphRequest) -> SignedCheckpoint:
        checkpoint, _state, _plan = self._lifecycle.load_verified(self._compile(request.plan), request)
        return checkpoint

    def available_commands(self, *, plan: ExecutionPlan, checkpoint: SignedCheckpoint) -> tuple[str, ...]:
        hints = getattr(self._policy, "available_commands", None)
        if not callable(hints):
            return ()
        return tuple(hints(plan=plan, state=NativeRunState.from_workflow_state(checkpoint.state)))

    def stream(
        self, request: NativeGraphRequest, *, after_sequence: int = 0, limit: int | None = None
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        request.assert_valid()
        return tuple(
            self._events.list_events(
                tenant_id=request.plan.tenant_id,
                run_id=request.run_id,
                after_sequence=max(0, int(after_sequence)),
                limit=limit,
            )
        )

    def _compile(self, plan: ExecutionPlan) -> ExecutionPlan:
        return self._components.compile(plan) if self._components is not None else plan

    @staticmethod
    def _assert_request_state_binding(
        request: NativeGraphRequest,
        checkpoint: SignedCheckpoint,
        state: NativeRunState,
    ) -> None:
        if state.input_data != request.input_data:
            raise ValueError("native_graph_input_binding_mismatch")
        if request.plan.metadata.get("bpmn_definition_hash") or request.plan.metadata.get("bpmn_activation_expansion"):
            start, deadline = state.bpmn_started_at, state.bpmn_deadline_at
            if any(type(value) not in {int, float} or not math.isfinite(value) for value in (start, deadline)):
                raise ValueError("bpmn_deadline_binding_required")
            if start < 0 or deadline != start + request.plan.budget.timeout_seconds:
                raise ValueError("bpmn_deadline_binding_mismatch")
        if tuple(sorted(checkpoint.state.secret_refs)) != tuple(sorted(request.secret_refs)):
            raise ValueError("native_graph_secret_refs_binding_mismatch")
        if (
            state.tenant_parallel_limit != request.tenant_parallel_limit
            or state.worker_parallel_limit != request.worker_parallel_limit
        ):
            raise ValueError("native_graph_parallel_limit_binding_mismatch")

