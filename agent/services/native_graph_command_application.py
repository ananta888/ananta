"""Application of signed operator commands (including safe plan edits) to Native graph runs.

``NativeGraphCommandApplier`` is a collaborator composed by
``NativeGraphOrchestrator``; plan compilation and validation are injected as
callables so the applier does not depend on the orchestrator itself.
"""

from __future__ import annotations

from collections.abc import Callable

from agent.services.bpmn_native_event_runtime import BpmnNativeEventRuntime
from agent.services.native_graph_models import (
    NativeGraphEventEmitterPort,
    NativeGraphRequest,
    NativeGraphRunControlPort,
    NativeGraphValidation,
    NativeRunState,
    WorkflowPlanArtifactPort,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan


class NativeGraphCommandApplier:
    """Apply verified workflow commands to a Native graph run."""

    def __init__(
        self,
        *,
        emitter: NativeGraphEventEmitterPort,
        run_control: NativeGraphRunControlPort,
        bpmn_events: BpmnNativeEventRuntime,
        plan_artifacts: WorkflowPlanArtifactPort | None,
        compile_plan: Callable[[ExecutionPlan], ExecutionPlan],
        validate_plan: Callable[[ExecutionPlan], NativeGraphValidation],
    ) -> None:
        self._emitter = emitter
        self._run_control = run_control
        self._bpmn_events = bpmn_events
        self._plan_artifacts = plan_artifacts
        self._compile_plan = compile_plan
        self._validate_plan = validate_plan

    def apply(
        self,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        command: SignedWorkflowCommand,
    ) -> ExecutionPlan:
        if command.command_type == "bpmn_message":
            delivery = self._bpmn_events.deliver_message(
                **self._run_control.bpmn_event_hooks(plan, request, state),
                command=command,
            )
            self._emitter.emit(
                state,
                plan=plan,
                request=request,
                step_id=command.step_id,
                actor=command.actor_id,
                event_type="workflow.bpmn.message.accepted",
                dedupe_key=f"native:{request.run_id}:{command.command_id}:message",
                payload={
                    "message_id": delivery.message_id,
                    "status": delivery.status,
                    "activation_id": delivery.target.activation_id,
                },
            )
            return plan
        if command.command_type in {"approve", "reject", "resume"}:
            gate_id = next((gate for gate, node in state.open_gates.items() if node == command.step_id), "")
            if command.command_type == "resume" and not gate_id and state.status == "paused":
                state.status = "running"
                self._emitter.emit(
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
                self._emitter.emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=command.step_id,
                    actor=command.actor_id,
                    event_type="workflow.approval.rejected",
                    dedupe_key=f"native:{request.run_id}:{command.command_id}:rejected",
                    payload={"gate_id": gate_id, "command_id": command.command_id},
                )
                self._run_control.fail_run(plan, request, state, "native_approval_rejected")
                return plan
            if command.command_type == "resume" and gate.gate_type != "resume":
                raise ValueError("native_resume_gate_type_mismatch")
            if command.command_type == "approve" and gate.gate_type == "resume":
                raise ValueError("native_resume_command_required")
            state.approved_gates.add(gate_id)
            state.open_gates.pop(gate_id, None)
            state.status = "running"
            self._emitter.emit(
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
                self._emitter.emit(
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
            self._emitter.emit(
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
            self._run_control.cancel_running(plan, request, state, "native_operator_cancel")
            state.status = "cancelled"
            state.reason_code = "native_operator_cancelled"
            self._emitter.emit(
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
            if any(node.node_type == "bpmn_wait" for node in plan.nodes):
                raise ValueError("bpmn_wait_closed_run_retry_denied")
            if state.status not in {"failed", "cancelled"}:
                raise ValueError("native_retry_terminal_failure_required")
            target = str(command.step_id or "").strip()
            if target and target != "__workflow__":
                state.failed.pop(target, None)
            else:
                state.failed.clear()
            state.status = "running"
            state.reason_code = ""
            self._emitter.emit(
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
            assert_safe_plan_edit(plan, replacement, state)
            replacement = self._compile_plan(replacement)
            validation = self._validate_plan(replacement)
            if not validation.valid:
                raise ValueError(f"native_replacement_plan_invalid:{','.join(validation.reason_codes)}")
            state.plan_revision += 1
            state.effective_plan = replacement.to_dict()
            self._emitter.emit(
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


def assert_safe_plan_edit(current: ExecutionPlan, replacement: ExecutionPlan, state: NativeRunState) -> None:
    """Reject plan edits that would rewrite executed work, bindings, policy, or capabilities."""

    if (
        current.metadata.get("bpmn_definition_hash") or current.metadata.get("bpmn_activation_expansion")
    ) and replacement.plan_hash != current.plan_hash:
        raise ValueError("bpmn_running_definition_immutable")
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
