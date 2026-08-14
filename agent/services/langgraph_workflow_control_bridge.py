"""Hub-owned control bridge and scheduler for production LangGraph workers.

LangGraph workers execute exactly one signed Hub task. The bridge owns DAG
scheduling, approval/cancel propagation and deterministic merge state. Its
complete restart state lives in ``WorkflowControlBindingStore`` plus the Hub
task repository; no process-local orchestration loop is required.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from agent.common.audit import log_audit
from agent.services.langgraph_workflow_plan_edit import (
    assert_safe_plan_edit,
    replace_status_plan,
    replacement_plan,
)
from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterQueueError,
    WorkflowAdapterTaskQueuePort,
    WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_control_authorization_helpers import (
    assert_route_control_envelope,
)
from agent.services.workflow_control_bindings import (
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandRejectedError,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_control_service import (
    RuntimeSelection,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecisionPort,
    WorkflowProviderRequirement,
    trusted_model_routing_from_metadata,
)
from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.condition_evaluator import (
    DeclarativeConditionEvaluator,
)
from agent.services.workflow_runtime.execution_plan import (
    ExecutionNode,
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.parallel import (
    BoundedFanOutScheduler,
    BranchResult,
    DeterministicMergeService,
    ParallelCapacityPort,
)
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
    LANGGRAPH_HUB_NODE_RESULT_SCHEMA,
    validate_langgraph_node_result,
)

LANGGRAPH_RUNTIME_ID = "langgraph"
_TERMINAL = frozenset({"completed", "failed", "cancelled", "skipped"})
_ACTIVE = frozenset({"created", "queued", "running", "assigned", "in_progress"})


class LangGraphWorkflowControlBridge:
    """Translate runtime-neutral control calls into bounded Hub node tasks."""

    runtime_id = LANGGRAPH_RUNTIME_ID
    selection_runtime_id = LANGGRAPH_RUNTIME_ID

    def __init__(
        self,
        *,
        queue: WorkflowAdapterTaskQueuePort,
        bindings: WorkflowControlBindingStore,
        command_verifier: WorkflowCommandVerifier,
        provider_decisions: WorkflowProviderDecisionPort,
        read_models: WorkflowControlReadModelProjector | None = None,
        fan_out: BoundedFanOutScheduler | None = None,
        merge: DeterministicMergeService | None = None,
        conditions: DeclarativeConditionEvaluator | None = None,
        clock=time.time,
        reconciler_id: str = "",
        capacity: ParallelCapacityPort | None = None,
    ) -> None:
        self._queue = queue
        self._bindings = bindings
        self._commands = command_verifier
        self._providers = provider_decisions
        self._read_models = read_models
        self._fan_out = fan_out or BoundedFanOutScheduler()
        self._merge = merge or DeterministicMergeService()
        self._conditions = conditions or DeclarativeConditionEvaluator()
        self._clock = clock
        self._reconciler_id = str(reconciler_id or f"langgraph-reconciler-{uuid.uuid4().hex}")
        self._capacity = capacity

    def start(
        self,
        *,
        principal: WorkflowPrincipal,
        plan: ExecutionPlan,
        run_id: str,
        selection: RuntimeSelection,
        authorization_envelope: dict[str, Any],
    ) -> WorkflowRunHandle:
        binding = self._require_binding(plan.workflow_id)
        self._assert_binding(binding, principal=principal, run_id=run_id, plan=plan)
        assert_route_control_envelope(
            authorization_envelope,
            principal=principal,
            workflow_id=plan.workflow_id,
            run_id=run_id,
        )
        if selection.runtime_id != self.selection_runtime_id:
            raise ValueError("workflow_control_runtime_binding_mismatch")
        status = self._initial_status(binding, plan=plan)
        status = self._advance(binding, plan=plan, status=status)
        self._bindings.record_status(binding.workflow_id, status)
        self._project(
            binding,
            status,
            mode=selection.mode,
            capabilities=tuple(sorted(selection.capabilities)),
        )
        return WorkflowRunHandle(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=self.runtime_id,
            status=str(status["status"]),
            task_ref=binding.run_id,
            reason_code=str(status.get("reason_code") or ""),
        )

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]:
        binding = self._require_run_binding(run_id)
        self._assert_principal(binding, principal)
        return deepcopy(self._current_status(binding, plan=self._plan(binding)))

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        processed = 0
        failed: list[dict[str, str]] = []
        for binding in self._bindings.claim_reconcilable(
            runtime_id=self.runtime_id,
            owner_id=self._reconciler_id,
            lease_seconds=30.0,
            limit=limit,
        ):
            try:
                plan = self._plan(binding)
                previous = self._current_status(binding, plan=plan)
                expected_revision = int(previous.get("revision") or 0)
                expected_checkpoint = str(previous.get("checkpoint_ref") or binding.checkpoint_id)
                status = self._advance(
                    binding,
                    plan=plan,
                    status=previous,
                )
                self._bindings.finish_reconciliation(
                    binding.workflow_id,
                    owner_id=self._reconciler_id,
                    expected_revision=expected_revision,
                    expected_checkpoint_ref=expected_checkpoint,
                    status=status,
                )
                self._project(binding, status)
                processed += 1
            except Exception as exc:  # one run cannot halt the Hub reconciler
                self._bindings.release_reconciliation(
                    binding.workflow_id,
                    owner_id=self._reconciler_id,
                )
                failed.append(
                    {
                        "workflow_id": binding.workflow_id,
                        "error_type": type(exc).__name__,
                    }
                )
        return {
            "runtime_id": self.runtime_id,
            "processed": processed,
            "failed": failed,
        }

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        if command.command_type == "cancel":
            raise ValueError("langgraph_signal_cancel_requires_cancel_port")
        return self._apply_command(principal=principal, command=command)

    def cancel(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        if command.command_type != "cancel":
            raise ValueError("langgraph_cancel_command_required")
        return self._apply_command(principal=principal, command=command)

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        binding = self._require_run_binding(run_id)
        self._assert_principal(binding, principal)
        status = self._current_status(binding, plan=self._plan(binding))
        events = [deepcopy(value) for value in status.get("events") or ()]
        known: set[str] = {str(value.get("event_id") or "") for value in events}
        for step in status.get("steps") or ():
            task_id = str(step.get("hub_task_id") or "")
            if not task_id:
                continue
            for event in self._queue.history(
                tenant_id=binding.tenant_id,
                subject_id=binding.subject_id,
                hub_task_id=task_id,
            ):
                event_id = str(event.get("event_id") or "")
                if event_id and event_id in known:
                    continue
                known.add(event_id)
                events.append(dict(event))
        ordered = sorted(
            events,
            key=lambda value: (
                float(value.get("occurred_at") or value.get("timestamp") or 0),
                str(value.get("event_id") or ""),
            ),
        )
        projected = []
        for sequence, event in enumerate(ordered, start=1):
            if sequence <= max(0, int(after_sequence)):
                continue
            projected.append({**event, "sequence": sequence})
        return tuple(projected)

    def _apply_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
        admitted_replay: bool = False,
    ) -> dict[str, Any]:
        try:
            binding = self._require_command_binding(command, principal)
            self._bindings.claim_command(
                binding.workflow_id,
                expected_revision=command.expected_revision,
                checkpoint_id=command.checkpoint_id,
                command_id=command.command_id,
            )
            plan = self._plan(binding)
            verifier = self._commands.verify_persisted if admitted_replay else self._commands.verify_once
            verifier(
                command,
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
                step_id=command.step_id,
                checkpoint_id=command.checkpoint_id,
                expected_revision=command.expected_revision,
                plan_hash=plan.plan_hash,
                policy_version=binding.policy_version,
            )
            status = self._current_status(binding, plan=plan)
            status = self._command_transition(
                binding,
                plan=plan,
                status=status,
                command=command,
            )
            self._bindings.finish_command(
                binding.workflow_id,
                command_id=command.command_id,
                status=status,
            )
        except (PermissionError, ValueError) as exc:
            self._bindings.release_command(
                command.workflow_id,
                command_id=command.command_id,
            )
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        except Exception:
            self._bindings.release_command(
                command.workflow_id,
                command_id=command.command_id,
            )
            raise
        self._project(binding, status)
        return status

    def recover_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        """Replay a persisted admission through idempotent Hub queue adapters."""

        return self._apply_command(
            principal=principal,
            command=command,
            admitted_replay=True,
        )

    def _command_transition(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
        status: dict[str, Any],
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        value = deepcopy(status)
        effective_plan = plan
        if command.command_type == "cancel":
            for step in value["steps"]:
                if step["status"] not in _TERMINAL and step.get("hub_task_id"):
                    task = self._queue.cancel(
                        tenant_id=binding.tenant_id,
                        subject_id=binding.subject_id,
                        hub_task_id=str(step["hub_task_id"]),
                        reason=str(command.payload.get("reason") or "workflow_cancelled"),
                    )
                    if str(task.get("status") or "").lower() != "cancelled":
                        step.update(
                            self._node_outcome(
                                task,
                                node_id=str(step["step_id"]),
                                plan_hash=plan.plan_hash,
                            )
                        )
                if step["status"] not in _TERMINAL:
                    step["status"] = "cancelled"
                    step["reason_code"] = "workflow_cancelled"
            states = {str(step.get("status") or "") for step in value["steps"]}
            if states <= {"completed", "skipped"}:
                value.update(status="completed", reason_code="")
            else:
                value.update(status="cancelled", reason_code="workflow_cancelled")
        elif command.command_type == "approve":
            node = self._node(plan, command.step_id)
            if not node.gate_id:
                raise ValueError("langgraph_approval_gate_required")
            step = self._step(value, command.step_id)
            if step.get("status") != "waiting_for_approval":
                raise ValueError("langgraph_approval_gate_not_open")
            gate = next(
                (candidate for candidate in plan.gates if candidate.gate_id == node.gate_id),
                None,
            )
            if gate is None:
                raise ValueError("langgraph_approval_gate_not_declared")
            if gate.required_roles and not set(gate.required_roles).intersection(command.actor_roles):
                raise PermissionError("langgraph_approval_gate_role_denied")
            value["approved_gates"] = sorted({*value.get("approved_gates", []), node.gate_id})
            value = self._advance(binding, plan=plan, status=value)
        elif command.command_type == "reject":
            step = self._step(value, command.step_id)
            step["status"] = "failed"
            step["reason_code"] = "approval_rejected"
            value["status"] = "failed"
            value["reason_code"] = "approval_rejected"
        elif command.command_type == "pause":
            value["paused"] = True
            value["status"] = "paused"
            value["reason_code"] = "workflow_paused"
        elif command.command_type == "resume":
            plan.assert_valid()
            value["paused"] = False
            value["reason_code"] = ""
            value = self._advance(binding, plan=plan, status=value)
        elif command.command_type in {"edit", "request_changes"}:
            if any(step.get("status") in _ACTIVE for step in value["steps"]):
                raise ValueError("langgraph_plan_edit_running_tasks_denied")
            effective_plan = replacement_plan(command)
            assert_safe_plan_edit(plan, effective_plan, value)
            replace_status_plan(value, current=plan, replacement=effective_plan)
            if command.command_type == "request_changes":
                value.update(
                    paused=True,
                    status="paused",
                    reason_code="workflow_changes_requested",
                )
            elif not value.get("paused"):
                value = self._advance(binding, plan=effective_plan, status=value)
        else:
            raise ValueError("langgraph_control_command_unsupported")
        self._append_control_event(
            value,
            event_type=f"workflow.control.{command.command_type}",
            step_id=command.step_id,
            reason_code=str(value.get("reason_code") or ""),
        )
        return self._bump(value, plan=effective_plan)

    def _advance(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        value = self._observe(binding, plan=plan, status=status, acknowledge=True)
        if value.get("paused") or value.get("status") in _TERMINAL:
            return self._bump(value, plan=plan)
        self._settle_hub_nodes(plan, value)
        steps = {str(step["step_id"]): step for step in value["steps"]}
        completed = {node_id for node_id, step in steps.items() if step["status"] in {"completed", "skipped"}}
        running = {node_id for node_id, step in steps.items() if step["status"] in _ACTIVE}
        failed = {node_id for node_id, step in steps.items() if step["status"] in {"failed", "cancelled"}}
        limits = self._parallel_limits(binding, plan=plan)
        batch = self._fan_out.select_ready(
            plan,
            completed_node_ids=completed,
            running_node_ids=running,
            failed_node_ids=failed,
            tenant_limit=limits[1],
            worker_limit=limits[2],
            plan_limit=limits[0],
            run_id=binding.run_id,
            capacity=self._capacity,
        )
        approved = set(value.get("approved_gates") or ())
        command = str(binding.request.metadata.get("adapter_command") or "execute")
        for candidate in batch.candidates:
            node = self._node(plan, candidate.node_id)
            step = steps[node.node_id]
            if node.node_type == "merge":
                continue
            if node.gate_id and node.gate_id not in approved:
                step["status"] = "waiting_for_approval"
                step["reason_code"] = f"approval_required:{node.gate_id}"
                continue
            receipt = self._queue.submit(
                self._submission(
                    binding,
                    plan=plan,
                    node=node,
                    status=value,
                    command=command,
                    limits=limits,
                )
            )
            step["hub_task_id"] = receipt.hub_task_id
            step["status"] = str(receipt.status or "created")
            step["reason_code"] = str(receipt.reason_code or "")
            self._append_control_event(
                value,
                event_type="workflow.node.delegated",
                step_id=node.node_id,
                reason_code="hub_task_created",
                hub_task_id=receipt.hub_task_id,
            )
        self._settle_status(value)
        return self._bump(value, plan=plan)

    def _observe(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
        status: dict[str, Any],
        acknowledge: bool,
    ) -> dict[str, Any]:
        value = deepcopy(status)
        read = self._queue.status if acknowledge else self._queue.inspect
        for step in value["steps"]:
            task_id = str(step.get("hub_task_id") or "")
            if not task_id or step["status"] in _TERMINAL:
                continue
            task = read(
                tenant_id=binding.tenant_id,
                subject_id=binding.subject_id,
                hub_task_id=task_id,
            )
            task_status = str(task.get("status") or "created").lower()
            if task_status in _ACTIVE:
                step["status"] = task_status
                continue
            outcome = self._node_outcome(
                task,
                node_id=str(step["step_id"]),
                plan_hash=plan.plan_hash,
            )
            step.update(outcome)
        self._settle_hub_nodes(plan, value)
        self._settle_status(value)
        return value

    def _settle_hub_nodes(self, plan: ExecutionPlan, status: dict[str, Any]) -> None:
        steps = {str(step["step_id"]): step for step in status["steps"]}
        incoming = self._incoming(plan)
        changed = True
        while changed:
            changed = False
            for node in sorted(plan.nodes, key=lambda item: item.node_id):
                step = steps[node.node_id]
                if step["status"] != "pending":
                    continue
                dependencies = incoming[node.node_id]
                if not dependencies or any(steps[edge.source]["status"] not in _TERMINAL for edge in dependencies):
                    continue
                if node.node_type == "merge":
                    result = self._merge.merge(
                        [
                            BranchResult(
                                edge.source,
                                str(steps[edge.source]["status"]),
                                steps[edge.source].get("value"),
                                str(steps[edge.source].get("reason_code") or ""),
                            )
                            for edge in dependencies
                        ],
                        strategy=str(node.metadata.get("merge_strategy") or "object-by-node-id"),
                        partial_failure=str(node.metadata.get("partial_failure") or "fail"),
                    )
                    step.update(
                        {
                            "status": result.status,
                            "value": result.value,
                            "reason_code": result.reason_code,
                            "failed_branches": list(result.failed_branches),
                        }
                    )
                    changed = True
                    continue
                route = self._route(node, dependencies, steps, status)
                if route is False:
                    step.update(status="skipped", reason_code="route_not_selected")
                    changed = True
                elif route is None:
                    step.update(status="failed", reason_code="condition_evaluation_failed")
                    changed = True
                elif any(steps[edge.source]["status"] in {"failed", "cancelled"} for edge in dependencies):
                    failed = sorted(
                        edge.source for edge in dependencies if steps[edge.source]["status"] in {"failed", "cancelled"}
                    )
                    step.update(
                        status="failed",
                        reason_code=f"upstream_failed:{','.join(failed)}",
                    )
                    changed = True

    def _route(
        self,
        node: ExecutionNode,
        incoming: tuple[Any, ...],
        steps: Mapping[str, Mapping[str, Any]],
        status: Mapping[str, Any],
    ) -> bool | None:
        context = {
            "input": dict(status.get("workflow_input") or {}),
            "results": {
                node_id: step.get("value")
                for node_id, step in sorted(steps.items())
                if step.get("status") == "completed"
            },
            "status": "running",
        }
        results = [self._conditions.evaluate(edge.condition, context) for edge in incoming]
        if any(result.value is None for result in results):
            return None
        if node.metadata.get("join_mode") == "all":
            return all(result.matches for result in results)
        return any(result.matches for result in results)

    def _submission(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
        node: ExecutionNode,
        status: Mapping[str, Any],
        command: str,
        limits: tuple[int, int, int],
    ) -> WorkflowAdapterTaskSubmission:
        requires_provider = command == "execute"
        model_routing = trusted_model_routing_from_metadata(node.metadata)
        decision = self._providers.decide(
            WorkflowProviderRequirement(
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                step_id=node.node_id,
                task_type=str(binding.request.metadata.get("adapter_task_type") or "agent_workflow"),
                runtime_kind=self.runtime_id,
                requires_provider=requires_provider,
                required_capabilities=tuple(node.required_capabilities),
                model_routing=model_routing,
            )
        )
        if requires_provider and decision.binding is None:
            raise WorkflowAdapterQueueError(
                f"workflow_adapter_provider_selection_unavailable:{decision.reason_code}",
                status_code=503,
            )
        dependencies = {
            edge.source: self._step(status, edge.source).get("value")
            for edge in self._incoming(plan)[node.node_id]
            if self._step(status, edge.source).get("status") == "completed"
        }
        metadata = binding.request.metadata
        retry = int(self._step(status, node.node_id).get("retry") or 0)
        return WorkflowAdapterTaskSubmission(
            tenant_id=binding.tenant_id,
            subject_id=binding.subject_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=node.node_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            adapter_kind=self.runtime_id,
            command=command,
            task_type=str(metadata.get("adapter_task_type") or "agent_workflow"),
            payload={
                **dict(metadata.get("adapter_payload") or {}),
                "schema": LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
                "execution_scope": "single_hub_node",
                "execution_plan": plan.to_dict(),
                "delegated_node_id": node.node_id,
                "workflow_input": dict(status.get("workflow_input") or {}),
                "dependency_results": dependencies,
                "approved_gates": list(status.get("approved_gates") or ()),
                "parallel_limits": {
                    "plan": limits[0],
                    "tenant": limits[1],
                    "worker": limits[2],
                },
            },
            allowed_tools=tuple(node.allowed_tools),
            allowed_artifacts=tuple(node.output_artifacts),
            correlation_id=str(binding.request.correlation_id or binding.run_id),
            idempotency_key=(f"langgraph:{binding.run_id}:{node.node_id}:{retry}:{plan.plan_hash}")[:256],
            maximum_retries=max(0, int((node.budget or plan.budget).max_attempts) - 1),
            # Provider reservations are aggregate and SQL-CAS guarded by
            # tenant/run/policy. Every branch therefore carries the same plan
            # ceiling instead of multiplying a per-branch allowance.
            max_total_tokens=int(plan.budget.max_tokens or 0),
            max_cost_micros=int(plan.budget.max_cost_micros or 0),
            authorization_ttl_seconds=min(
                86_400.0,
                max(60.0, float((node.budget or plan.budget).timeout_seconds)),
            ),
            provider_binding=decision.binding,
            provider_decision_reason=decision.reason_code,
            primary_profile_id=decision.primary_profile_id,
            provider_profile_bindings=decision.profile_bindings,
            provider_attempt_plan=decision.profile_attempt_plan,
            provider_maximum_attempts=decision.maximum_provider_attempts,
            model_routing=(model_routing.as_metadata() if model_routing is not None else {}),
        )

    @staticmethod
    def _node_outcome(task: Mapping[str, Any], *, node_id: str, plan_hash: str) -> dict[str, Any]:
        task_status = str(task.get("status") or "failed").lower()
        result = task.get("result")
        reason = str(task.get("reason_code") or "")
        if isinstance(result, Mapping):
            reason = str(result.get("reason_code") or reason)
            adapter_result = result.get("adapter_result")
            if isinstance(adapter_result, Mapping):
                for artifact in adapter_result.get("artifacts") or ():
                    if not isinstance(artifact, Mapping):
                        continue
                    if artifact.get("schema") == LANGGRAPH_HUB_NODE_RESULT_SCHEMA:
                        validate_langgraph_node_result(artifact)
                        if str(artifact.get("node_id") or "") != node_id:
                            raise WorkflowAdapterQueueError("langgraph_node_result_binding_mismatch", status_code=409)
                        if str(artifact.get("plan_hash") or "") != plan_hash:
                            raise WorkflowAdapterQueueError(
                                "langgraph_node_result_plan_binding_mismatch",
                                status_code=409,
                            )
                        return {
                            "status": str(artifact.get("status") or "failed"),
                            "reason_code": str(artifact.get("reason_code") or reason),
                            "value": artifact.get("value"),
                            "artifacts": dict(artifact.get("artifacts") or {}),
                            "tokens": int(artifact.get("tokens") or 0),
                            "cost_micros": int(artifact.get("cost_micros") or 0),
                        }
        return {
            "status": "cancelled" if task_status == "cancelled" else "failed",
            "reason_code": reason or "langgraph_node_result_missing",
        }

    def _initial_status(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
    ) -> dict[str, Any]:
        metadata = binding.request.metadata
        raw_input = metadata.get("input_data") or metadata.get("parameters") or {}
        value = {
            "schema": "ananta.workflow_backend_status.v1",
            "backend": self.runtime_id,
            "runtime_id": self.runtime_id,
            "runtime_version": "1.0.0",
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "hub_task_id": binding.run_id,
            "status": "created",
            "reason": "",
            "reason_code": "",
            "revision": 0,
            "checkpoint_ref": binding.checkpoint_id,
            "plan_hash": plan.plan_hash,
            "paused": False,
            "approved_gates": [],
            "workflow_input": dict(raw_input) if isinstance(raw_input, Mapping) else {},
            "steps": [
                {
                    "id": node.node_id,
                    "step_id": node.node_id,
                    "task_kind": node.task_kind,
                    "gate_id": node.gate_id,
                    "status": "pending",
                    "reason_code": "",
                    "hub_task_id": "",
                    "retry": 0,
                    "value": None,
                    "artifacts": {},
                }
                for node in plan.nodes
            ],
            "events": [],
            "updated_at": float(self._clock()),
        }
        self._append_control_event(
            value,
            event_type="workflow.run.started",
            reason_code="hub_control_started",
        )
        return value

    def _current_status(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
    ) -> dict[str, Any]:
        value = self._bindings.last_status(binding.workflow_id)
        if value is None:
            return self._initial_status(binding, plan=plan)
        if str(value.get("plan_hash") or "") != plan.plan_hash:
            raise RuntimeError("workflow_control_plan_binding_mismatch")
        return dict(value)

    def _settle_status(self, status: dict[str, Any]) -> None:
        states = {str(step.get("status") or "pending") for step in status["steps"]}
        if status.get("paused"):
            status.update(status="paused", reason_code="workflow_paused")
        elif states <= {"completed", "skipped"}:
            status.update(status="completed", reason="", reason_code="")
        elif "cancelled" in states and not states.intersection(_ACTIVE):
            status.update(status="cancelled", reason_code="workflow_cancelled")
        elif "failed" in states and not states.intersection(_ACTIVE | {"pending", "waiting_for_approval"}):
            status.update(status="failed", reason_code="langgraph_node_failed")
        elif "waiting_for_approval" in states and not states.intersection(_ACTIVE):
            status.update(status="waiting_for_approval", reason_code="approval_required")
        elif states.intersection(_ACTIVE):
            status.update(status="running", reason="", reason_code="")
        else:
            status.update(status="running", reason="", reason_code="")

    def _bump(self, status: dict[str, Any], *, plan: ExecutionPlan) -> dict[str, Any]:
        value = deepcopy(status)
        revision = int(value.get("revision") or 0) + 1
        value["revision"] = revision
        value["checkpoint_ref"] = f"langgraph:{plan.plan_hash}:{revision}"
        value["updated_at"] = float(self._clock())
        return value

    def _append_control_event(
        self,
        status: dict[str, Any],
        *,
        event_type: str,
        reason_code: str,
        step_id: str = "",
        hub_task_id: str = "",
    ) -> None:
        values = list(status.get("events") or ())
        identity = len(values) + 1
        values.append(
            {
                "event_id": f"lg-control:{status.get('run_id')}:{identity}",
                "workflow_id": str(status.get("workflow_id") or ""),
                "run_id": str(status.get("run_id") or ""),
                "step_id": step_id,
                "event_type": event_type,
                "timestamp": float(self._clock()),
                "details": {
                    "runtime_id": self.runtime_id,
                    "reason_code": reason_code,
                    "hub_task_id": hub_task_id,
                },
            }
        )
        status["events"] = values[-256:]

    @staticmethod
    def _incoming(plan: ExecutionPlan) -> dict[str, tuple[Any, ...]]:
        return {
            node.node_id: tuple(
                sorted(
                    (edge for edge in plan.edges if edge.target == node.node_id),
                    key=lambda edge: edge.source,
                )
            )
            for node in plan.nodes
        }

    @staticmethod
    def _parallel_limits(
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan,
    ) -> tuple[int, int, int]:
        metadata = binding.request.metadata
        raw = (
            plan.metadata.get("parallel_limit", 4),
            metadata.get("tenant_parallel_limit", 4),
            metadata.get("worker_parallel_limit", 4),
        )
        try:
            values = tuple(int(value) for value in raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("langgraph_parallel_limit_invalid") from exc
        if any(value < 1 or value > 64 for value in values):
            raise ValueError("langgraph_parallel_limit_invalid")
        return values  # type: ignore[return-value]

    def _plan(self, binding: WorkflowControlRunBinding) -> ExecutionPlan:
        persisted = self._bindings.last_status(binding.workflow_id) or {}
        effective = persisted.get("effective_plan")
        raw_plan = (
            effective
            if isinstance(effective, Mapping)
            else binding.execution_plan or binding.request.metadata.get("execution_plan")
        )
        if isinstance(raw_plan, Mapping):
            plan = ExecutionPlan.from_mapping(dict(raw_plan))
            plan.assert_valid()
            expected_hash = str(persisted.get("plan_hash") or binding.plan_hash)
            if (
                plan.tenant_id != binding.tenant_id
                or plan.workflow_id != binding.workflow_id
                or plan.policy_version != binding.policy_version
                or plan.plan_hash != expected_hash
            ):
                raise PermissionError("workflow_control_plan_binding_mismatch")
            return plan
        return WorkflowRequestExecutionPlanAdapter.adapt(
            binding.request,
            tenant_id=binding.tenant_id,
            policy_version=binding.policy_version,
        )

    @staticmethod
    def _node(plan: ExecutionPlan, node_id: str) -> ExecutionNode:
        node = next((value for value in plan.nodes if value.node_id == node_id), None)
        if node is None:
            raise ValueError("workflow_control_step_binding_mismatch")
        return node

    @staticmethod
    def _step(status: Mapping[str, Any], node_id: str) -> dict[str, Any]:
        step = next(
            (value for value in status.get("steps") or () if str(value.get("step_id") or "") == str(node_id)),
            None,
        )
        if not isinstance(step, dict):
            raise ValueError("workflow_control_step_binding_mismatch")
        return step

    def _require_command_binding(
        self,
        command: SignedWorkflowCommand,
        principal: WorkflowPrincipal,
    ) -> WorkflowControlRunBinding:
        binding = self._require_binding(command.workflow_id)
        self._assert_principal(binding, principal)
        status = self._current_status(binding, plan=self._plan(binding))
        if (
            binding.run_id != command.run_id
            or str(status.get("plan_hash") or "") != command.plan_hash
            or binding.policy_version != command.policy_version
            or str(status.get("checkpoint_ref") or "") != command.checkpoint_id
            or int(status.get("revision") or 0) != command.expected_revision
        ):
            raise PermissionError("workflow_control_command_binding_mismatch")
        return binding

    def _require_binding(self, workflow_id: str) -> WorkflowControlRunBinding:
        binding = self._bindings.get(workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return binding

    def _require_run_binding(self, run_id: str) -> WorkflowControlRunBinding:
        binding = self._bindings.get_by_run_id(run_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return binding

    @staticmethod
    def _assert_binding(
        binding: WorkflowControlRunBinding,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        plan: ExecutionPlan,
    ) -> None:
        LangGraphWorkflowControlBridge._assert_principal(binding, principal)
        if binding.runtime_id != LANGGRAPH_RUNTIME_ID:
            raise PermissionError("workflow_control_runtime_binding_mismatch")
        if binding.run_id != str(run_id) or binding.plan_hash != plan.plan_hash:
            raise PermissionError("workflow_control_run_binding_mismatch")
        if binding.policy_version != plan.policy_version:
            raise PermissionError("workflow_control_policy_binding_mismatch")

    @staticmethod
    def _assert_principal(
        binding: WorkflowControlRunBinding,
        principal: WorkflowPrincipal,
    ) -> None:
        if binding.tenant_id != principal.tenant_id or binding.subject_id != principal.subject_id:
            raise PermissionError("workflow_control_principal_binding_mismatch")

    def _project(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
        *,
        mode: str = "live",
        capabilities: tuple[str, ...] = (),
    ) -> None:
        if self._read_models is None:
            return
        try:
            self._read_models.project(
                binding=binding,
                status=status,
                runtime=self.runtime_id,
                mode=mode,
                capabilities=capabilities,
            )
        except Exception as exc:
            log_audit(
                "workflow_runtime_read_model_projection_failed",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "runtime": self.runtime_id,
                    "error_type": type(exc).__name__,
                },
            )


__all__ = [
    "LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA",
    "LANGGRAPH_HUB_NODE_RESULT_SCHEMA",
    "LANGGRAPH_RUNTIME_ID",
    "LangGraphWorkflowControlBridge",
]
