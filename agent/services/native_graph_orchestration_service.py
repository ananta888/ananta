"""Hub-owned orchestration for the Ananta Native graph runtime.

This service is intentionally a resumable state-machine tick.  It performs only
control-plane work (routing, gates, fan-out selection, deterministic merge,
ownership and persistence).  Every executable task node is submitted through
``HubTaskQueuePort``; the service has no in-process worker fallback.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.services.native_graph_checkpoint_service import NativeGraphCheckpointService
from agent.services.native_graph_delegation_service import NativeGraphDelegationService
from agent.services.native_graph_models import (
    NATIVE_GRAPH_RUNTIME_ID,
    NATIVE_GRAPH_RUNTIME_VERSION,
    NATIVE_GRAPH_TERMINAL_STATUSES,
    NativeControlPolicyPort,
    NativeGraphRequest,
    NativeGraphResult,
    NativeGraphValidation,
    NativeRunState,
    WorkflowPlanArtifactPort,
    safe_native_reason_code,
)
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
    validate_compiled_component_output,
)
from agent.services.workflow_runtime.condition_evaluator import DeclarativeConditionEvaluator
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.native_graph_contracts import (
    NativeNodeResult,
)
from agent.services.workflow_runtime.native_graph_ports import HubTaskQueuePort
from agent.services.workflow_runtime.ownership import ExecutionOwnershipStore
from agent.services.workflow_runtime.parallel import (
    BoundedFanOutScheduler,
    BranchResult,
    DeterministicMergeService,
)
from agent.services.workflow_runtime.persistence import CheckpointStore
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    SignedCheckpoint,
)
from agent.services.workflow_runtime.side_effects import SideEffectLedger, side_effect_event

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
        clock=time.time,
    ) -> None:
        self._queue = queue
        self._checkpoints = checkpoints
        self._events = events
        self._ownership = ownership
        self._ledger = ledger
        self._key_ring = key_ring
        grants = authorization_grants or InMemoryWorkflowAuthorizationGrantService(clock=clock)
        self._commands = command_verifier
        self._policy = policy
        self._components = component_compiler
        self._plan_artifacts = plan_artifacts
        self._clock = clock
        self._delegation = NativeGraphDelegationService(
            queue=queue,
            ownership=ownership,
            ledger=ledger,
            key_ring=key_ring,
            authorization_grants=grants,
            provider_decisions=(provider_decisions or build_workflow_provider_decision_service()),
            clock=clock,
        )
        self._checkpoint_service = NativeGraphCheckpointService(
            checkpoints=checkpoints,
            key_ring=key_ring,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            clock=clock,
            compile_plan=self._compile,
        )
        self._conditions = DeclarativeConditionEvaluator()
        self._fan_out = BoundedFanOutScheduler()
        self._merge = DeterministicMergeService()

    def validate(self, plan: ExecutionPlan) -> NativeGraphValidation:
        reasons = [issue.code for issue in plan.validate()]
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
            if node.node_type not in {"task", "merge", "checkpoint", "component"}:
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
        )
        self._emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.started",
            dedupe_key=f"native:{request.run_id}:started",
            payload={"runtime_id": self.runtime_id, "plan_hash": plan.plan_hash},
        )
        self._tick(plan, request, state)
        checkpoint = self._save_checkpoint(plan, request, state)
        return self._result(plan, request, state, checkpoint)

    def advance(self, request: NativeGraphRequest) -> NativeGraphResult:
        request.assert_valid()
        requested_plan = self._compile(request.plan)
        checkpoint, state, plan = self._load_verified(requested_plan, request)
        self._assert_request_state_binding(request, checkpoint, state)
        if state.status in _TERMINAL:
            return self._result(plan, request, state, checkpoint)
        if state.status != "running":
            return self._result(plan, request, state, checkpoint)
        self._tick(plan, request, state)
        updated = self._save_checkpoint(plan, request, state)
        return self._result(plan, request, state, updated)

    def inspect(self, request: NativeGraphRequest) -> NativeGraphResult:
        """Read the latest verified state without ticking or persisting it."""

        request.assert_valid()
        requested_plan = self._compile(request.plan)
        checkpoint, state, plan = self._load_verified(requested_plan, request)
        self._assert_request_state_binding(request, checkpoint, state)
        return self._result(plan, request, state, checkpoint)

    def resume(
        self,
        request: NativeGraphRequest,
        *,
        command: SignedWorkflowCommand,
        checkpoint: SignedCheckpoint | None = None,
        admitted_replay: bool = False,
    ) -> NativeGraphResult:
        request.assert_valid()
        requested_plan = self._compile(request.plan)
        if checkpoint is None:
            current, state, plan = self._load_verified(requested_plan, request)
        else:
            current = checkpoint
            state = NativeRunState.from_workflow_state(current.state)
            plan = self._effective_plan(requested_plan, state, current)
            self._verify_checkpoint(current, plan, request)
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
            return self._result(plan, request, state, current)
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
        plan = self._apply_command(plan, request, state, command)
        if state.status == "running":
            self._tick(plan, request, state)
        state.last_command_id = command.command_id
        state.last_command_fingerprint = command_fingerprint
        updated = self._save_checkpoint(plan, request, state)
        return self._result(plan, request, state, updated)

    def checkpoint(self, request: NativeGraphRequest) -> SignedCheckpoint:
        checkpoint, _state, _plan = self._load_verified(self._compile(request.plan), request)
        return checkpoint

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
        if tuple(sorted(checkpoint.state.secret_refs)) != tuple(sorted(request.secret_refs)):
            raise ValueError("native_graph_secret_refs_binding_mismatch")
        if (
            state.tenant_parallel_limit != request.tenant_parallel_limit
            or state.worker_parallel_limit != request.worker_parallel_limit
        ):
            raise ValueError("native_graph_parallel_limit_binding_mismatch")

    def _tick(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if state.status != "running":
            return
        self._collect_results(plan, request, state)
        if state.status != "running":
            return
        self._resolve_conditional_skips(plan, request, state)
        if state.status != "running":
            return
        self._execute_ready_merges(plan, request, state)
        if state.status != "running":
            return
        self._dispatch_ready(plan, request, state)
        self._finish_if_terminal(plan, request, state)

    def _collect_results(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if not state.running:
            return
        results = self._queue.poll(
            tenant_id=plan.tenant_id,
            run_id=request.run_id,
            hub_task_ids=tuple(sorted(item["hub_task_id"] for item in state.running.values())),
        )
        nodes = {node.node_id: node for node in plan.nodes}
        for result in sorted(results, key=lambda item: (item.node_id, item.result_id)):
            running = state.running.get(result.node_id)
            if running is None:
                continue
            self._assert_result_binding(plan, request, result, running)
            node = nodes[result.node_id]
            owner_values = {
                "tenant_id": plan.tenant_id,
                "run_id": request.run_id,
                "step_id": result.node_id,
                "attempt_id": result.attempt_id,
                "owner_id": str(running["owner_id"]),
                "fencing_token": result.fencing_token,
                "expected_revision": int(running["ownership_revision"]),
                "now": float(self._clock()),
            }
            state.running.pop(result.node_id, None)
            if result.status == "completed":
                validate_compiled_component_output(node, result.output_data)
                acknowledged = self._ownership.acknowledge_result(
                    **owner_values,
                    result_ack_key=result.result_id,
                )
                self._validate_artifacts(node, result)
                self._consume_budget(plan, state, result)
                state.completed.add(result.node_id)
                state.node_results[result.node_id] = dict(result.output_data)
                state.artifact_refs.update(result.artifact_refs)
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=result.node_id,
                    attempt=acknowledged.fencing_token,
                    event_type="workflow.step.completed",
                    dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:completed",
                    payload={
                        "artifact_ids": sorted(result.artifact_refs),
                        "budget_usage": dict(result.budget_usage),
                    },
                )
                self._emit_side_effect_if_present(plan, request, state, result, running)
                continue
            failure = result.reason_code or f"native_node_{result.status}"
            fail_attempt = getattr(self._ownership, "fail_attempt", None)
            if fail_attempt is None:
                raise RuntimeError("native_ownership_failure_transition_unavailable")
            failed_owner = fail_attempt(
                **owner_values,
                failure_code=failure,
                dead_letter=False,
            )
            attempt_count = state.attempts.get(result.node_id, 1)
            maximum = (node.budget or plan.budget).max_attempts
            if attempt_count < maximum:
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=result.node_id,
                    attempt=failed_owner.fencing_token,
                    event_type="workflow.step.retry_scheduled",
                    dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:retry",
                    payload={"reason_code": failure, "next_attempt": attempt_count + 1},
                )
                continue
            state.failed[result.node_id] = failure
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=result.node_id,
                attempt=failed_owner.fencing_token,
                event_type="workflow.step.failed",
                dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:failed",
                payload={"reason_code": failure},
            )
            if str(node.metadata.get("failure_policy") or "fail") != "continue":
                self._fail_run(plan, request, state, failure)
                return

    def _resolve_conditional_skips(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState
    ) -> None:
        terminal = state.completed | state.skipped | set(state.failed)
        incoming: dict[str, list[Any]] = {node.node_id: [] for node in plan.nodes}
        for edge in plan.edges:
            incoming[edge.target].append(edge)
        changed = True
        while changed:
            changed = False
            terminal = state.completed | state.skipped | set(state.failed)
            context = self._condition_context(state)
            for node in sorted(plan.nodes, key=lambda item: item.node_id):
                if node.node_id in terminal or node.node_id in state.running or not incoming[node.node_id]:
                    continue
                if not all(edge.source in terminal for edge in incoming[node.node_id]):
                    continue
                evaluations = [self._conditions.evaluate(edge.condition, context) for edge in incoming[node.node_id]]
                if any(result.value is None for result in evaluations):
                    reason = next(result.reason_code for result in evaluations if result.value is None)
                    self._fail_run(plan, request, state, reason)
                    return
                mode = str(node.metadata.get("join_mode") or "any")
                route_matches = (
                    all(result.matches for result in evaluations)
                    if mode == "all"
                    else any(result.matches for result in evaluations)
                )
                if route_matches:
                    continue
                state.skipped.add(node.node_id)
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.step.skipped",
                    dedupe_key=f"native:{request.run_id}:{node.node_id}:route-skipped",
                    payload={"reason_code": "native_route_not_selected"},
                )
                changed = True

    def _execute_ready_merges(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        incoming: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
        for edge in plan.edges:
            incoming[edge.target].append(edge.source)
        terminal = state.completed | state.skipped | set(state.failed)
        for node in sorted(plan.nodes, key=lambda item: item.node_id):
            if node.node_type != "merge" or node.node_id in terminal or node.node_id in state.running:
                continue
            sources = incoming[node.node_id]
            if not sources or not set(sources).issubset(terminal):
                continue
            branches = [
                BranchResult(
                    node_id=source,
                    status="completed" if source in state.completed else "failed",
                    value=state.node_results.get(source),
                    reason_code=state.failed.get(source, "native_branch_skipped"),
                )
                for source in sources
            ]
            merged = self._merge.merge(
                branches,
                strategy=str(node.metadata.get("merge_strategy") or ""),
                partial_failure=str(node.metadata.get("partial_failure") or "fail"),
            )
            if merged.status != "completed":
                state.failed[node.node_id] = merged.reason_code
                self._fail_run(plan, request, state, merged.reason_code)
                return
            state.completed.add(node.node_id)
            state.node_results[node.node_id] = merged.value
            for artifact_id in node.output_artifacts:
                state.artifact_refs[artifact_id] = f"artifact://native/{request.run_id}/{node.node_id}/{artifact_id}"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=node.node_id,
                event_type="workflow.step.completed",
                dedupe_key=f"native:{request.run_id}:{node.node_id}:merged",
                payload={
                    "merge_strategy": node.metadata.get("merge_strategy"),
                    "failed_branches": list(merged.failed_branches),
                },
            )
            terminal.add(node.node_id)

    def _dispatch_ready(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        batch = self._fan_out.select_ready(
            plan,
            completed_node_ids=state.completed | state.skipped,
            running_node_ids=set(state.running),
            failed_node_ids=set(state.failed),
            tenant_limit=request.tenant_parallel_limit,
            worker_limit=request.worker_parallel_limit,
        )
        nodes = {node.node_id: node for node in plan.nodes}
        for candidate in batch.candidates:
            node = nodes[candidate.node_id]
            if node.node_type == "merge":
                continue
            if not self._route_matches(plan, node, state):
                continue
            if node.gate_id and node.gate_id not in state.approved_gates:
                self._open_gate(plan, request, state, node)
                continue
            if node.node_type == "checkpoint":
                state.completed.add(node.node_id)
                state.node_results[node.node_id] = {"checkpoint": "hub-owned"}
                for artifact_id in node.output_artifacts:
                    state.artifact_refs[artifact_id] = (
                        f"checkpoint://{plan.tenant_id}/{request.run_id}/{request.control_task_id}"
                    )
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.step.completed",
                    dedupe_key=f"native:{request.run_id}:{node.node_id}:checkpoint-node",
                    payload={"checkpoint_policy": "hub-owned"},
                )
                continue
            allowed, reason = self._policy.authorize_delegation(plan=plan, node=node, state=state)
            if not allowed:
                self._fail_run(plan, request, state, reason or "native_delegation_policy_denied")
                return
            self._submit_node(plan, request, state, node)

    def _submit_node(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, node: ExecutionNode
    ) -> None:
        self._delegation.submit(
            plan=plan,
            request=request,
            state=state,
            node=node,
            input_data=self._node_input(node, state),
            fail=self._fail_run,
            emit=self._emit,
        )

    def _open_gate(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, node: ExecutionNode
    ) -> None:
        if node.gate_id in state.open_gates:
            return
        state.open_gates[node.gate_id] = node.node_id
        self._emit(
            state,
            plan=plan,
            request=request,
            step_id=node.node_id,
            event_type="workflow.approval.requested",
            dedupe_key=f"native:{request.run_id}:{node.gate_id}:requested",
            payload={"gate_id": node.gate_id},
        )
        gate = next(gate for gate in plan.gates if gate.gate_id == node.gate_id)
        if gate.gate_type == "resume":
            state.status = "paused"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=node.node_id,
                event_type="workflow.run.paused",
                dedupe_key=f"native:{request.run_id}:{node.gate_id}:paused",
                payload={"gate_id": node.gate_id},
            )
        else:
            state.status = "waiting_for_approval"

    def _apply_command(
        self,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        command: SignedWorkflowCommand,
    ) -> ExecutionPlan:
        if command.command_type in {"approve", "reject", "resume"}:
            gate_id = next((gate for gate, node in state.open_gates.items() if node == command.step_id), "")
            if command.command_type == "resume" and not gate_id and state.status == "paused":
                state.status = "running"
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=command.step_id,
                    actor=command.actor_id,
                    event_type="workflow.run.resumed",
                    dedupe_key=f"native:{request.run_id}:{command.command_id}:resumed",
                    payload={"command_id": command.command_id},
                )
                return plan
            if not gate_id:
                raise ValueError("native_command_gate_not_open")
            gate = next(gate for gate in plan.gates if gate.gate_id == gate_id)
            if gate.required_roles and not set(gate.required_roles).intersection(command.actor_roles):
                raise PermissionError("native_command_gate_role_denied")
            if command.command_type == "reject":
                state.open_gates.pop(gate_id, None)
                state.failed[command.step_id] = "native_approval_rejected"
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=command.step_id,
                    actor=command.actor_id,
                    event_type="workflow.approval.rejected",
                    dedupe_key=f"native:{request.run_id}:{command.command_id}:rejected",
                    payload={"gate_id": gate_id, "command_id": command.command_id},
                )
                self._fail_run(plan, request, state, "native_approval_rejected")
                return plan
            if command.command_type == "resume" and gate.gate_type != "resume":
                raise ValueError("native_resume_gate_type_mismatch")
            if command.command_type == "approve" and gate.gate_type == "resume":
                raise ValueError("native_resume_command_required")
            state.approved_gates.add(gate_id)
            state.open_gates.pop(gate_id, None)
            state.status = "running"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.approval.granted",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:granted",
                payload={"gate_id": gate_id, "command_id": command.command_id},
            )
            if command.command_type == "resume":
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=command.step_id,
                    actor=command.actor_id,
                    event_type="workflow.run.resumed",
                    dedupe_key=f"native:{request.run_id}:{command.command_id}:resumed",
                    payload={"gate_id": gate_id},
                )
            return plan
        if command.command_type == "pause":
            state.status = "paused"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.run.paused",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:paused",
                payload={"command_id": command.command_id},
            )
            return plan
        if command.command_type == "cancel":
            self._cancel_running(plan, request, state, "native_operator_cancel")
            state.status = "cancelled"
            state.reason_code = "native_operator_cancelled"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.run.cancelled",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:cancelled",
                payload={
                    "command_id": command.command_id,
                    "reason_code": state.reason_code,
                },
            )
            return plan
        if command.command_type == "retry":
            if state.status not in {"failed", "cancelled"}:
                raise ValueError("native_retry_terminal_failure_required")
            target = str(command.step_id or "").strip()
            if target and target != "__workflow__":
                state.failed.pop(target, None)
            else:
                state.failed.clear()
            state.status = "running"
            state.reason_code = ""
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.run.retry_requested",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:retry",
                payload={"command_id": command.command_id},
            )
            return plan
        if command.command_type in {"edit", "request_changes"}:
            if state.running:
                raise ValueError("native_plan_edit_running_tasks_denied")
            replacement = self._replacement_plan(command)
            if replacement.plan_hash != str(command.payload.get("replacement_plan_hash") or ""):
                raise ValueError("native_replacement_plan_hash_mismatch")
            self._assert_safe_plan_edit(plan, replacement, state)
            replacement = self._compile(replacement)
            validation = self.validate(replacement)
            if not validation.valid:
                raise ValueError(f"native_replacement_plan_invalid:{','.join(validation.reason_codes)}")
            state.plan_revision += 1
            state.effective_plan = replacement.to_dict()
            self._emit(
                state,
                plan=replacement,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.plan.edited",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:plan-edited",
                payload={
                    "previous_plan_hash": plan.plan_hash,
                    "plan_hash": replacement.plan_hash,
                    "plan_revision": state.plan_revision,
                    "command_type": command.command_type,
                },
            )
            if command.command_type == "request_changes":
                state.status = "paused"
            return replacement
        raise ValueError("native_command_type_unsupported")

    def _replacement_plan(self, command: SignedWorkflowCommand) -> ExecutionPlan:
        if isinstance(command.payload.get("replacement_plan"), dict):
            return ExecutionPlan.from_mapping(dict(command.payload["replacement_plan"]))
        plan_ref = str(command.payload.get("plan_ref") or "")
        if not plan_ref or self._plan_artifacts is None:
            raise ValueError("native_plan_artifact_resolver_required")
        return self._plan_artifacts.load_plan(tenant_id=command.tenant_id, plan_ref=plan_ref)

    @staticmethod
    def _assert_safe_plan_edit(current: ExecutionPlan, replacement: ExecutionPlan, state: NativeRunState) -> None:
        if replacement.tenant_id != current.tenant_id or replacement.workflow_id != current.workflow_id:
            raise ValueError("native_plan_edit_binding_mismatch")
        if replacement.policy_version != current.policy_version:
            raise ValueError("native_plan_edit_policy_change_denied")
        if set(replacement.capabilities) - set(current.capabilities):
            raise ValueError("native_plan_edit_capability_escalation")
        current_nodes = {node.node_id: node for node in current.nodes}
        replacement_nodes = {node.node_id: node for node in replacement.nodes}
        for node_id in state.completed | set(state.running):
            missing = node_id not in replacement_nodes
            changed = not missing and replacement_nodes[node_id].to_dict() != current_nodes[node_id].to_dict()
            if missing or changed:
                raise ValueError("native_plan_edit_executed_node_changed")

    def _route_matches(self, plan: ExecutionPlan, node: ExecutionNode, state: NativeRunState) -> bool:
        edges = [edge for edge in plan.edges if edge.target == node.node_id]
        if not edges:
            return True
        results = [self._conditions.evaluate(edge.condition, self._condition_context(state)) for edge in edges]
        if any(result.value is None for result in results):
            return False
        return (
            all(result.matches for result in results)
            if node.metadata.get("join_mode") == "all"
            else any(result.matches for result in results)
        )

    @staticmethod
    def _condition_context(state: NativeRunState) -> dict[str, Any]:
        return {
            "input": dict(state.input_data),
            "results": dict(state.node_results),
            "artifacts": dict(state.artifact_refs),
            "status": state.status,
        }

    @staticmethod
    def _node_input(node: ExecutionNode, state: NativeRunState) -> dict[str, Any]:
        return {
            "workflow_input": dict(state.input_data),
            "dependency_results": {key: state.node_results[key] for key in sorted(state.node_results)},
            "requested_artifacts": list(node.input_artifacts),
        }

    @staticmethod
    def _validate_artifacts(node: ExecutionNode, result: NativeNodeResult) -> None:
        unexpected = set(result.artifact_refs) - set(node.output_artifacts)
        missing = set(node.output_artifacts) - set(result.artifact_refs)
        if unexpected:
            raise ValueError("native_node_artifact_undeclared")
        if missing:
            raise ValueError("native_node_artifact_missing")

    def _consume_budget(self, plan: ExecutionPlan, state: NativeRunState, result: NativeNodeResult) -> None:
        for key, value in result.budget_usage.items():
            state.budget_usage[key] = state.budget_usage.get(key, 0) + value
        limits = {
            "tokens": plan.budget.max_tokens,
            "cost_micros": plan.budget.max_cost_micros,
        }
        for key, limit in limits.items():
            if limit is not None and state.budget_usage.get(key, 0) > limit:
                raise ValueError(f"native_budget_exceeded:{key}")

    @staticmethod
    def _assert_result_binding(
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        result: NativeNodeResult,
        running: dict[str, Any],
    ) -> None:
        result.assert_valid()
        expected = {
            "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id,
            "run_id": request.run_id,
            "command_id": running["command_id"],
            "hub_task_id": running["hub_task_id"],
            "attempt_id": running["attempt_id"],
            "fencing_token": running["fencing_token"],
        }
        if any(getattr(result, key) != value for key, value in expected.items()):
            raise ValueError("native_node_result_binding_mismatch")

    def _emit_side_effect_if_present(
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
        stored = self._events.append(event, expected_sequence=state.event_sequence)
        state.event_sequence = stored.sequence

    def _finish_if_terminal(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if state.status != "running" or state.running or state.open_gates:
            return
        terminal = state.completed | state.skipped | set(state.failed)
        if len(terminal) != len(plan.nodes):
            return
        required = {artifact.artifact_id for artifact in plan.artifacts if artifact.required}
        missing = required - set(state.artifact_refs)
        if missing:
            self._fail_run(plan, request, state, f"native_required_artifacts_missing:{','.join(sorted(missing))}")
            return
        state.status = "completed"
        self._emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.completed",
            dedupe_key=f"native:{request.run_id}:completed",
            payload={"artifact_ids": sorted(state.artifact_refs)},
        )

    def _fail_run(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, reason: str) -> None:
        self._cancel_running(plan, request, state, reason)
        state.status = "failed"
        state.reason_code = safe_native_reason_code(reason)
        self._emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.run.failed",
            dedupe_key=f"native:{request.run_id}:failed:{state.event_sequence + 1}",
            payload={"reason_code": state.reason_code},
        )

    def _cancel_running(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, reason: str
    ) -> None:
        self._delegation.cancel_running(
            plan=plan,
            request=request,
            state=state,
            reason=reason,
        )

    def _save_checkpoint(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState
    ) -> SignedCheckpoint:
        return self._checkpoint_service.save(
            plan=plan,
            request=request,
            state=state,
            emit=self._emit,
        )

    def _load_verified(
        self,
        requested_plan: ExecutionPlan,
        request: NativeGraphRequest,
    ) -> tuple[SignedCheckpoint, NativeRunState, ExecutionPlan]:
        return self._checkpoint_service.load_verified(
            requested_plan=requested_plan,
            request=request,
        )

    def _effective_plan(
        self,
        requested_plan: ExecutionPlan,
        state: NativeRunState,
        checkpoint: SignedCheckpoint,
    ) -> ExecutionPlan:
        return self._checkpoint_service.effective_plan(
            requested_plan=requested_plan,
            state=state,
            checkpoint=checkpoint,
        )

    def _verify_checkpoint(
        self, checkpoint: SignedCheckpoint, plan: ExecutionPlan, request: NativeGraphRequest
    ) -> None:
        self._checkpoint_service.verify(
            checkpoint=checkpoint,
            plan=plan,
            request=request,
        )

    def _emit(
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
        stored = self._events.append(event, expected_sequence=state.event_sequence)
        state.event_sequence = stored.sequence
        return stored

    @staticmethod
    def _result(
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
