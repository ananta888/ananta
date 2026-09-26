"""Injectable Hub Native adapter for signed, bounded BPMN event waits.

Parent hooks (all keyword-only): arm(..., node), reconcile(...),
deliver_message(..., command), cancel_run(...). Common arguments are plan,
request, state, fencing_token, guard and persist. ``guard()`` must validate the
CURRENT run-control lease, bound to tenant/run/owner/expiry and the supplied
fence. ``persist()`` durably checkpoints this state under that same ownership
and the revision loaded by the parent. A guard lookup alone is not an atomic
ownership transaction; production composition owns the lease/write boundary.

Native state owns only catch intents and the applied receipt set:
``bpmn_waits: dict[str, dict]`` and ``bpmn_applied_wakeups: set[str]``. The signed
wait store remains the sole source for deadlines, inboxes and consumed receipts.
Call reconcile before successor dispatch; append its returned receipts using
their stable dedupe keys. Receipts replay after crashes, including the window
between wait consumption and Native checkpoint persistence.

deliver_message requires a NEW ``bpmn_message`` SignedWorkflowCommand already
verified and authorized by the parent's existing command infrastructure. Its
payload is closed; tenant/run/plan/actor/target authority comes from the verified
envelope and signed Native state, never payload fields. Early delivery is denied.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.services.bpmn_event_wait import BpmnEventWaitService
from agent.services.bpmn_event_wait_contracts import (
    CatchActivation,
    EventWaitConflict,
    EventWaitError,
    InboundMessage,
    MessageContract,
    MessageDelivery,
    TimerSpec,
    WaitLimits,
    WaitRunBinding,
    WaitView,
    Wakeup,
    identity,
)
from agent.services.bpmn_event_wait_store import WAIT_CHECKPOINT_TASK, EventWaitStore
from agent.services.native_graph_models import NativeGraphRequest, NativeRunState
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.visual_process.bpmn_event_definitions import validate_event_definition, validate_message_payload


@dataclass(frozen=True)
class _SchemaValidator:
    schemas: dict[str, dict]

    def validate(self, schema_id: str, payload: dict[str, Any]) -> None:
        if schema_id not in self.schemas:
            raise EventWaitError("bpmn_wait_schema_unknown")
        validate_message_payload(self.schemas[schema_id], payload)


@dataclass(frozen=True)
class _CommandAuthority:
    """Exact delivery scope derived only after the parent verifies the command."""

    command: SignedWorkflowCommand | None
    expected: InboundMessage | None = None

    def authorize(self, message: InboundMessage) -> None:
        if self.command is None or self.expected != message:
            raise PermissionError("bpmn_wait_verified_command_required")
        identity(self.command.actor_id, "actor_id")


def wait_run_binding(plan: ExecutionPlan, request: NativeGraphRequest) -> WaitRunBinding:
    """All authority is already admitted and pinned in the immutable Hub plan."""
    if request.control_task_id == WAIT_CHECKPOINT_TASK:
        raise EventWaitError("bpmn_wait_control_namespace_collision")
    project, definition = event_plan_scope(plan)
    return WaitRunBinding(
        plan.tenant_id, project, plan.workflow_id, request.run_id, definition, plan.plan_hash, plan.policy_version
    )


def event_plan_scope(plan: ExecutionPlan) -> tuple[str, str]:
    """Validate Hub scope before Native creates a run or performs any effects."""
    scope = plan.metadata.get("workflow_rollout_scope", {})
    project = plan.metadata.get("project_id") or (scope.get("project_id") if isinstance(scope, dict) else None)
    if not project:
        raise EventWaitError("bpmn_wait_project_binding_required")
    definition = plan.metadata.get("bpmn_definition_hash")
    if not definition:
        raise EventWaitError("bpmn_wait_definition_binding_required")
    identity(project, "project_id")
    identity(definition, "definition_revision")
    return project, definition


class BpmnNativeEventRuntime:
    """No dispatch, worker orchestration, polling loop or ambient authority."""

    def __init__(self, *, store: EventWaitStore, clock: Callable[[], float], limits: WaitLimits = WaitLimits()):
        self._store = store
        self._clock = clock
        self._limits = limits

    def arm(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        node: ExecutionNode,
        fencing_token: int,
        guard: Callable[[], None],
        persist: Callable[[], Any],
    ) -> WaitView:
        guard()
        self._active(state)
        if node.node_id in state.completed or node.node_id in state.skipped or node.node_id in state.failed:
            raise EventWaitError("bpmn_wait_target_inactive")
        definition = self._definition(plan, node)
        run = wait_run_binding(plan, request)
        target = self._target(run, node)
        intent = state.bpmn_waits.get(node.node_id)
        if intent is None:
            if len(state.bpmn_waits) >= self._limits.max_activations:
                raise EventWaitError("bpmn_wait_activation_capacity_exceeded")
            intent = {
                "activation_id": target.activation_id,
                "definition_hash": sha256_json(definition),
                "wakeup_id": "",
            }
            state.bpmn_waits[node.node_id] = intent
            try:
                guard()
                persist()
            except BaseException:
                state.bpmn_waits.pop(node.node_id)
                raise
        self._verify_intent(intent, target, definition)
        service = self._service(plan, request, fencing_token, guard, lease=state.control_lease)
        if service.is_closed():
            raise EventWaitError("bpmn_wait_run_closed")
        if definition["kind"] == "timer":
            view = service.arm_timer(
                target, TimerSpec(**definition["timer"]), timeout_seconds=definition["timeout_seconds"]
            )
        else:
            view = service.subscribe_message(
                target, self._contract(definition), timeout_seconds=definition["timeout_seconds"]
            )
        if intent["wakeup_id"] and intent["wakeup_id"] != view.wakeup_id:
            raise EventWaitConflict("bpmn_wait_wakeup_binding_mismatch")
        if not intent["wakeup_id"]:
            intent["wakeup_id"] = view.wakeup_id
            try:
                guard()
                persist()
            except BaseException:
                intent["wakeup_id"] = ""
                raise
        return view

    def reconcile(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        fencing_token: int,
        guard: Callable[[], None],
        persist: Callable[[], Any],
    ) -> tuple[Wakeup, ...]:
        guard()
        if state.status != "running" or not state.bpmn_waits:
            return ()
        self._active(state)
        nodes = {node.node_id: node for node in plan.nodes}
        service = self._service(plan, request, fencing_token, guard, lease=state.control_lease)
        if service.is_closed():
            raise EventWaitError("bpmn_wait_run_closed")
        receipts = []
        for node_id in sorted(state.bpmn_waits):
            if node_id not in nodes:
                raise EventWaitConflict("bpmn_wait_node_binding_mismatch")
            node = nodes[node_id]
            definition = self._definition(plan, node)
            target = self._target(service.run, node)
            intent = state.bpmn_waits[node_id]
            self._verify_intent(intent, target, definition)
            if node_id in state.skipped or node_id in state.failed:
                raise EventWaitConflict("bpmn_wait_target_inactive")
            if not intent["wakeup_id"]:
                self.arm(
                    plan=plan,
                    request=request,
                    state=state,
                    node=node,
                    fencing_token=fencing_token,
                    guard=guard,
                    persist=persist,
                )
            view = service.inspect(target)
            if view is None or view.wakeup_id != intent["wakeup_id"]:
                raise EventWaitConflict("bpmn_wait_snapshot_missing")
            if view.status in {"expired", "cancelled"}:
                guard()
                state.failed[node_id] = "bpmn_wait_" + view.status
                raise EventWaitError("bpmn_wait_" + view.status)
            receipt = service.consume(target, wakeup_id=intent["wakeup_id"])
            if receipt is None:
                continue
            if receipt.wakeup_id not in state.bpmn_applied_wakeups:
                if node_id in state.completed or node_id in state.node_results:
                    raise EventWaitConflict("bpmn_wait_application_conflict")
                guard()
                self._active(state)
                state.completed.add(node_id)
                state.bpmn_applied_wakeups.add(receipt.wakeup_id)
                state.node_results[node_id] = {
                    "event_kind": receipt.kind,
                    "message_id": receipt.message_id,
                    "payload": receipt.payload,
                }
                try:
                    guard()
                    persist()  # MUST precede event append and ANY successor dispatch.
                except BaseException:
                    state.completed.remove(node_id)
                    state.bpmn_applied_wakeups.remove(receipt.wakeup_id)
                    state.node_results.pop(node_id)
                    raise
            elif node_id not in state.completed:
                raise EventWaitConflict("bpmn_wait_applied_state_mismatch")
            receipts.append(receipt)
        return tuple(receipts)

    def deliver_message(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        command: SignedWorkflowCommand,
        fencing_token: int,
        guard: Callable[[], None],
        persist: Callable[[], Any],
    ) -> MessageDelivery:
        guard()
        self._active(state)
        run = wait_run_binding(plan, request)
        if not isinstance(command, SignedWorkflowCommand) or command.command_type != "bpmn_message":
            raise EventWaitError("bpmn_wait_message_command_required")
        if (command.tenant_id, command.workflow_id, command.run_id, command.plan_hash, command.policy_version) != (
            run.tenant_id,
            run.workflow_id,
            run.run_id,
            run.plan_hash,
            run.policy_version,
        ):
            raise EventWaitConflict("bpmn_wait_message_command_binding_mismatch")
        if not command.issued_at <= self._clock() < command.expires_at:
            raise EventWaitError("bpmn_wait_message_command_expired")
        identity(command.actor_id, "actor_id")
        payload = command.payload
        fields = {
            "activation_id",
            "message_id",
            "name",
            "correlation_key",
            "schema_id",
            "sent_at",
            "expires_at",
            "payload",
        }
        if type(payload) is not dict or set(payload) != fields:
            raise EventWaitError("bpmn_wait_message_command_payload_invalid")
        node = next((node for node in plan.nodes if node.node_id == command.step_id), None)
        if node is None:
            raise EventWaitError("bpmn_wait_message_target_unknown")
        definition = self._definition(plan, node)
        if definition["kind"] != "message":
            raise EventWaitError("bpmn_wait_message_target_invalid")
        intent = state.bpmn_waits.get(node.node_id)
        if intent is None or not intent["wakeup_id"]:
            raise EventWaitError("bpmn_wait_early_message_denied")
        if node.node_id in state.skipped or node.node_id in state.failed:
            raise EventWaitError("bpmn_wait_target_inactive")
        target = self._target(run, node)
        self._verify_intent(intent, target, definition)
        contract = self._contract(definition)
        if (payload["activation_id"], payload["name"], payload["correlation_key"], payload["schema_id"]) != (
            target.activation_id,
            contract.name,
            contract.correlation_key,
            contract.schema_id,
        ):
            raise EventWaitConflict("bpmn_wait_message_target_binding_mismatch")
        message = InboundMessage(
            run, target, contract, payload["message_id"], payload["sent_at"], payload["expires_at"], payload["payload"]
        )
        service = self._service(
            plan,
            request,
            fencing_token,
            guard,
            lease=state.control_lease,
            authority=_CommandAuthority(command, message),
        )
        return service.deliver_message(message)

    def cancel_run(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        fencing_token: int,
        guard: Callable[[], None],
        persist: Callable[[], Any],
    ) -> tuple[WaitView, ...]:
        guard()
        return self._service(plan, request, fencing_token, guard, lease=state.control_lease).cancel_run()

    def _service(self, plan, request, fence, guard, *, lease, authority=None):
        if not callable(guard):
            raise EventWaitError("bpmn_wait_run_guard_required")
        if lease is None or lease.fencing_token != fence:
            raise EventWaitError("bpmn_wait_run_lease_required")
        schemas = {}
        for node in plan.nodes:
            if node.node_type == "bpmn_wait":
                definition = self._definition(plan, node)
                if definition["kind"] == "message":
                    schemas[definition["schema_id"]] = definition["payload_schema"]
        return BpmnEventWaitService(
            run=wait_run_binding(plan, request),
            store=self._store.for_lease(lease),
            clock=self._clock,
            fencing_token=fence,
            guard=guard,
            limits=self._limits,
            authorizer=authority or _CommandAuthority(None),
            payload_validator=_SchemaValidator(schemas),
        )

    def _active(self, state):
        if state.status not in {"running", "paused", "waiting_for_approval"}:
            raise EventWaitError("bpmn_wait_run_inactive")
        if state.bpmn_deadline_at is not None and self._clock() >= state.bpmn_deadline_at:
            raise EventWaitError("bpmn_wait_run_deadline_exceeded")

    @staticmethod
    def _definition(plan, node):
        if node.node_type != "bpmn_wait" or not any(item == node for item in plan.nodes):
            raise EventWaitError("bpmn_wait_node_binding_mismatch")
        if (
            node.allowed_tools
            or node.input_artifacts
            or node.output_artifacts
            or node.gate_id
            or node.side_effect_class != "none"
        ):
            raise EventWaitError("bpmn_wait_worker_effects_forbidden")
        return validate_event_definition(node.metadata.get("bpmn_wait"))

    @staticmethod
    def _target(run, node):
        # Execution nodes are already activation-expanded by the Hub compiler.
        return CatchActivation(
            node.node_id,
            "bpmn-catch-"
            + sha256_json(
                {
                    "plan_hash": run.plan_hash,
                    "run_id": run.run_id,
                    "node_id": node.node_id,
                }
            ),
        )

    @staticmethod
    def _contract(definition):
        return MessageContract(definition["name"], definition["correlation_key"], definition["schema_id"])

    @staticmethod
    def _verify_intent(intent, target, definition):
        if (
            type(intent) is not dict
            or set(intent) != {"activation_id", "definition_hash", "wakeup_id"}
            or intent["activation_id"] != target.activation_id
            or intent["definition_hash"] != sha256_json(definition)
            or type(intent["wakeup_id"]) is not str
        ):
            raise EventWaitConflict("bpmn_wait_intent_binding_mismatch")
