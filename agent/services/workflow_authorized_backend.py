"""Request-scoped, principal-bound view of the composed workflow backend control boundary."""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol

from agent.common.audit import log_audit
from agent.services.bpmn_workflow_preflight import (
    BpmnWorkflowPreflight,
    assert_workflow_start_hashes,
    workflow_start_plan,
)
from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA, WorkflowRequest, WorkflowSignal
from agent.services.workflow_configured_backend_bridge import ConfiguredWorkflowBackendBridge
from agent.services.workflow_control_authorization_helpers import ROUTE_CONTROL_AUTHORIZATION_SCHEMA
from agent.services.workflow_control_bindings import WorkflowControlBindingStore, WorkflowControlRunBinding
from agent.services.workflow_control_command_guards import (
    _FAILED_START_STATUSES,
    _TRANSITION_DRIVE_ATTEMPTS,
    _assert_client_command_bindings,
    _assert_client_command_snapshot,
    _assert_restart_safe_start_adoption,
    _resolve_command_step_id,
)
from agent.services.workflow_control_command_receipts import (
    COMMAND_RECEIPT_COMPLETED,
    COMMAND_RECEIPT_REJECTED,
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptError,
    WorkflowControlCommandReceiptReconciler,
    WorkflowControlCommandReceiptStore,
    WorkflowControlCommandRejectedError,
    admitted_receipt_command,
    assert_stable_receipt_retry,
    validate_persisted_public_status,
)
from agent.services.workflow_control_command_receipts import status_revision as command_receipt_status_revision
from agent.services.workflow_control_dispatch_service import START_OBSERVATION_PENDING
from agent.services.workflow_control_service import (
    CONTROL_COMMAND_TYPES,
    WorkflowControlCommand,
    WorkflowControlService,
    WorkflowPrincipal,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan, WorkflowRequestExecutionPlanAdapter
from agent.services.workflow_runtime_bridge_registry import WorkflowRuntimeBridgeRegistry
from agent.services.workflow_runtime_selection_composition import configured_runtime_id
from agent.services.workflow_transition_native_composition import WorkflowCommandTransitionRuntime


class PersistedPublicStatusValidator(Protocol):
    """Validate a persisted public status against its immutable receipt and run binding."""

    def __call__(
        self,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
    ) -> None: ...


class AuthorizedWorkflowBackend:
    """Request-scoped compatibility view; it owns no orchestration state."""

    def __init__(
        self,
        *,
        control: WorkflowControlService,
        bridge: ConfiguredWorkflowBackendBridge,
        bindings: WorkflowControlBindingStore,
        command_receipts: WorkflowControlCommandReceiptStore,
        receipt_reconciler: WorkflowControlCommandReceiptReconciler,
        registry: WorkflowRuntimeBridgeRegistry,
        project_public_status: Any,
        principal: WorkflowPrincipal,
        transitions: WorkflowCommandTransitionRuntime | None = None,
        persisted_status_validator: PersistedPublicStatusValidator = validate_persisted_public_status,
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._command_receipts = command_receipts
        self._receipt_reconciler = receipt_reconciler
        self._registry = registry
        self._project_public_status = project_public_status
        self._principal = principal
        self._transitions = transitions
        self._validate_persisted_status = persisted_status_validator

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def start_workflow(
        self,
        request: WorkflowRequest,
        *,
        command_id: str = "",
        expected_plan_hash: str | None = None,
        expected_definition_hash: str | None = None,
    ) -> dict[str, Any]:
        plan = workflow_start_plan(request, tenant_id=self._principal.tenant_id)
        assert_workflow_start_hashes(
            request,
            plan,
            expected_plan_hash=expected_plan_hash,
            expected_definition_hash=expected_definition_hash,
        )
        run_id = str(request.metadata.get("run_id") or request.workflow_id).strip()
        binding = WorkflowControlRunBinding(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject_id,
            workflow_id=request.workflow_id,
            run_id=run_id,
            runtime_id="pending",
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            checkpoint_id=f"legacy-current:{plan.plan_hash[:24]}",
            request=request,
            execution_plan=plan.to_dict(),
        )
        created_binding = False
        existing = self._bindings.get(request.workflow_id)
        if existing is None:
            try:
                self._bindings.put(binding)
                created_binding = True
            except RuntimeError as exc:
                if str(exc) != "workflow_control_binding_already_exists":
                    raise
                existing = self._bindings.get(request.workflow_id)
                if existing is None:
                    raise
        if existing is not None:
            _assert_restart_safe_start_adoption(existing, binding)
            binding = existing
            persisted = self._bindings.last_status(request.workflow_id)
            if persisted is not None and str(persisted.get("status") or "").lower() != "pending":
                return self._public_status(binding, persisted)
        try:
            self._control.start(
                principal=self._principal,
                plan=plan,
                run_id=run_id,
                authorization_envelope=self._route_authorization(
                    binding,
                    start_request_id=str(command_id or ""),
                ),
                preferred_runtime=self._bridge.selection_runtime_id,
                allowed_runtimes=(self._bridge.selection_runtime_id,),
            )
        except Exception as exc:
            if created_binding and self.backend_id != "temporal" and str(exc) != START_OBSERVATION_PENDING:
                self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise
        binding = self._bindings.get(request.workflow_id) or binding
        status = self._bindings.last_status(request.workflow_id)
        if status is None:
            if created_binding and self.backend_id != "temporal":
                self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise RuntimeError("workflow_control_start_status_missing")
        if (
            created_binding
            and self.backend_id != "temporal"
            and str(status.get("status") or "").lower() in _FAILED_START_STATUSES
        ):
            self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
        return self._public_status(binding, status)

    def preflight_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        return BpmnWorkflowPreflight(self._control).evaluate(
            request,
            principal=self._principal,
            preferred_runtime=self._bridge.selection_runtime_id,
        )

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self._receipt_reconciler.reconcile_workflow(workflow_id)
        binding = self._bindings.get(workflow_id)
        run_id = binding.run_id if binding is not None else str(workflow_id)
        status = dict(
            self._control.query(
                principal=self._principal,
                workflow_id=str(workflow_id),
                run_id=run_id,
            )
        )
        return self._public_status(binding, status) if binding is not None else status

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        return self.command_workflow(
            workflow_id,
            command_type="cancel",
            payload={"reason": str(reason)},
        )

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        if signal.name not in CONTROL_COMMAND_TYPES - {"cancel"}:
            raise ValueError("workflow_control_command_type_unsupported")
        return self.command_workflow(
            workflow_id,
            command_type=signal.name,
            payload=dict(signal.payload),
        )

    def command_workflow(
        self,
        workflow_id: str,
        *,
        command_type: str,
        payload: dict[str, Any] | None = None,
        command_id: str = "",
        expected_revision: int | None = None,
        plan_hash: str | None = None,
        step_id: str | None = None,
        run_id: str | None = None,
        checkpoint_ref: str | None = None,
    ) -> dict[str, Any]:
        """Submit one canonical command through the sole Hub control service."""

        binding = self._bindings.get(workflow_id)
        if binding is None:
            return self._not_found(workflow_id)
        _assert_client_command_bindings(
            binding,
            command_type=command_type,
            command_id=command_id,
            expected_revision=expected_revision,
            plan_hash=plan_hash,
            step_id=step_id,
            run_id=run_id,
        )
        normalized_command_id = str(command_id or "").strip()
        runtime_id = configured_runtime_id(binding.runtime_id)
        if command_type in {"edit", "request_changes"}:
            raise WorkflowControlCommandRejectedError("workflow_plan_edit_rebind_required")
        if normalized_command_id and runtime_id != "temporal":
            existing_receipt = self._command_receipts.get(normalized_command_id)
            if existing_receipt is not None:
                if expected_revision is not None and expected_revision != existing_receipt.expected_revision:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict")
                if step_id is not None and step_id != existing_receipt.request_payload.get("step_id"):
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict")
                if checkpoint_ref is not None and checkpoint_ref != existing_receipt.checkpoint_ref:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict")
                try:
                    assert_stable_receipt_retry(
                        existing_receipt,
                        binding=binding,
                        actor_id=self._principal.subject_id,
                        command_type=command_type,
                        payload=dict(payload or {}),
                    )
                except WorkflowControlCommandReceiptError as exc:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                recovered = self._recover_command_receipt(existing_receipt)
                if recovered is not None:
                    return recovered
        if normalized_command_id and runtime_id == "temporal":
            try:
                repeated = self._registry.retry_command(
                    binding=binding,
                    command_id=normalized_command_id,
                    command_type=command_type,
                    payload=dict(payload or {}),
                    **({"expected_revision": expected_revision} if expected_revision is not None else {}),
                    **({"step_id": step_id} if step_id is not None else {}),
                    **({"checkpoint_ref": checkpoint_ref} if checkpoint_ref is not None else {}),
                )
            except RuntimeError as exc:
                if str(exc) == "workflow_control_dispatch_stage_conflict":
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                raise
            if repeated is not None:
                return dict(repeated)
        if runtime_id == "temporal":
            self.get_workflow_status(workflow_id)
        status = self._bindings.last_status(workflow_id) or {}
        _assert_client_command_snapshot(
            binding,
            status,
            expected_revision=expected_revision,
            checkpoint_ref=checkpoint_ref,
        )
        command = self._command(
            binding,
            command_type=command_type,
            payload=dict(payload or {}),
            command_id=normalized_command_id,
            expected_revision=expected_revision,
            step_id=step_id,
            checkpoint_ref=checkpoint_ref,
        )
        try:
            signed_command = self._control.prepare_command(
                principal=self._principal,
                command=command,
            )
        except (PermissionError, ValueError) as exc:
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        receipt: WorkflowControlCommandReceipt | None = None
        if normalized_command_id and runtime_id != "temporal":
            try:
                receipt = self._command_receipts.stage(
                    binding=binding,
                    command_id=normalized_command_id,
                    actor_id=self._principal.subject_id,
                    command_type=command.command_type,
                    request_payload=self._command_receipt_request(
                        command,
                        admitted=signed_command,
                    ),
                    expected_revision=command.expected_revision,
                    checkpoint_ref=command.checkpoint_id,
                )
            except WorkflowControlCommandReceiptError as exc:
                if str(exc) in {
                    "workflow_control_command_receipt_conflict",
                    "workflow_control_command_receipt_stage_conflict",
                    "workflow_control_command_receipt_replay_detected",
                }:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                raise
            if receipt.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(receipt.rejection_reason)
            if self._transitions is not None and not receipt.transition_id:
                # Admission attributes the receipt row itself under CAS, so the
                # transition and its command become one durable fact before any
                # effect runs.  A failure here must not fall through to the
                # unattributed dispatch path.
                self._transitions.admission.stage_or_adopt(receipt=receipt, binding=binding)
                receipt = self._command_receipts.get(receipt.command_id) or receipt
            recovered = self._recover_command_receipt(receipt)
            if recovered is not None:
                return recovered
        try:
            result = dict(
                self._control.dispatch_command(
                    principal=self._principal,
                    command=signed_command,
                )
            )
            result = self._public_status(binding, result)
        except WorkflowControlCommandRejectedError as exc:
            if receipt is not None:
                # Explicit-ID synchronous commands are dispatched from
                # ``_recover_command_receipt`` under a receipt lease.
                raise RuntimeError("workflow_control_command_receipt_lease_missing") from exc
            log_audit(
                "workflow_control_command_rejected",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "command_id": command.command_id,
                    "reason_code": exc.reason_code,
                },
            )
            raise
        except Exception:
            if receipt is not None:
                recovered = self._recover_command_receipt(receipt)
                if recovered is not None:
                    return recovered
            raise
        if receipt is None:
            return result
        raise RuntimeError("workflow_control_command_receipt_lease_missing")

    def _drive_pending_transition(
        self,
        command_id: str,
        binding: WorkflowControlRunBinding,
    ) -> dict[str, Any]:
        """Drive an attributed command to its terminal receipt, or fail closed.

        A receipt that carries a transition is owned by the transition runner,
        never by the synchronous dispatch path: claiming it here would race a
        live effect against its own fencing.  Driving is therefore the only
        legitimate move, and a transition that does not terminate within the
        bounded budget stays pending rather than reporting a status nothing
        has finalized.
        """

        if self._transitions is None:
            raise RuntimeError("workflow_control_command_transition_pending")
        for _ in range(_TRANSITION_DRIVE_ATTEMPTS):
            self._transitions.driver.tick()
            current = self._command_receipts.get(command_id)
            if current is None:
                raise RuntimeError("workflow_control_command_receipt_missing")
            if current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                self._validate_persisted_status(current, binding, persisted)
                return persisted
            if current.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(current.rejection_reason)
        raise RuntimeError("workflow_control_command_transition_pending")

    def _recover_command_receipt(
        self,
        receipt: WorkflowControlCommandReceipt,
    ) -> dict[str, Any] | None:
        binding = self._bindings.get(receipt.workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        if receipt.state == COMMAND_RECEIPT_COMPLETED:
            persisted = dict(receipt.result_status or {})
            self._validate_persisted_status(receipt, binding, persisted)
            return persisted
        if receipt.state == COMMAND_RECEIPT_REJECTED:
            raise WorkflowControlCommandRejectedError(receipt.rejection_reason)
        if receipt.transition_id:
            return self._drive_pending_transition(receipt.command_id, binding)
        owner_id = f"receipt-request:{uuid.uuid4().hex}"
        claimed = self._command_receipts.claim(
            receipt.command_id,
            owner_id=owner_id,
        )
        deadline = time.monotonic() + 5.0
        while claimed is None and time.monotonic() < deadline:
            current = self._command_receipts.get(receipt.command_id)
            if current is None:
                raise RuntimeError("workflow_control_command_receipt_missing")
            if current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                self._validate_persisted_status(current, binding, persisted)
                return persisted
            if current.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(current.rejection_reason)
            time.sleep(0.01)
            claimed = self._command_receipts.claim(
                receipt.command_id,
                owner_id=owner_id,
            )
        if claimed is None:
            raise RuntimeError("workflow_control_command_observation_pending")
        receipt = claimed
        status = self._bindings.last_status(receipt.workflow_id)
        if status is None or command_receipt_status_revision(status) <= receipt.expected_revision:
            command = admitted_receipt_command(receipt)
            try:
                status = dict(
                    self._registry.recover_command(
                        principal=WorkflowPrincipal(
                            tenant_id=receipt.tenant_id,
                            subject_id=receipt.actor_id,
                            roles=command.actor_roles,
                        ),
                        command=command,
                    )
                )
            except WorkflowControlCommandRejectedError as exc:
                self._command_receipts.reject(
                    receipt.command_id,
                    reason_code=exc.reason_code,
                    owner_id=owner_id,
                    dispatch_generation=receipt.dispatch_generation,
                )
                raise
        status = self._public_status(binding, status)
        receipt = self._command_receipts.heartbeat(
            receipt.command_id,
            owner_id=owner_id,
            dispatch_generation=receipt.dispatch_generation,
        )
        try:
            completed = self._command_receipts.complete(
                receipt.command_id,
                status=status,
                owner_id=owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
        except Exception:
            current = self._command_receipts.get(receipt.command_id)
            if current is not None and current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                self._validate_persisted_status(current, binding, persisted)
                return persisted
            # The runtime observation and canonical public status are already
            # durable. Releasing only this receipt lease makes a retry adopt
            # that state without replaying the runtime mutation.
            self._command_receipts.release(
                receipt.command_id,
                owner_id=owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
            raise
        return dict(completed.result_status or status)

    def _command_receipt_request(
        self,
        command: WorkflowControlCommand,
        *,
        admitted: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        return {
            "actor_roles": sorted(self._principal.roles),
            "admitted_command": admitted.to_dict(),
            "payload": dict(command.payload),
            "step_id": command.step_id,
        }

    def _public_status(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        return self._project_public_status(binding, status)

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        binding = self._bindings.get(workflow_id)
        run_id = binding.run_id if binding is not None else str(workflow_id)
        return list(
            self._control.history(
                principal=self._principal,
                workflow_id=str(workflow_id),
                run_id=run_id,
            )
        )

    def _command(
        self,
        binding: WorkflowControlRunBinding,
        *,
        command_type: str,
        payload: dict[str, Any],
        command_id: str = "",
        expected_revision: int | None = None,
        step_id: str | None = None,
        checkpoint_ref: str | None = None,
    ) -> WorkflowControlCommand:
        status = self._bindings.last_status(binding.workflow_id) or {}
        if step_id is None:
            step_id = _resolve_command_step_id(binding, status=status, payload=payload)
        if expected_revision is None:
            try:
                expected_revision = int(status.get("revision", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("workflow_control_revision_invalid") from exc
        if command_type == "bpmn_message":
            nodes = (binding.execution_plan or {}).get("nodes") or []
            if step_id not in {node.get("node_id") for node in nodes}:
                raise WorkflowControlCommandRejectedError("bpmn_message_target_invalid")
        checkpoint_id = (
            checkpoint_ref if checkpoint_ref is not None else str(status.get("checkpoint_ref") or binding.checkpoint_id)
        )
        return WorkflowControlCommand(
            command_id=str(command_id or f"legacy-control-{uuid.uuid4().hex}"),
            command_type=command_type,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=step_id,
            checkpoint_id=checkpoint_id,
            expected_revision=expected_revision,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            authorization_envelope=self._route_authorization(binding),
            payload=dict(payload),
        )

    def _plan(self, binding: WorkflowControlRunBinding) -> ExecutionPlan:
        return WorkflowRequestExecutionPlanAdapter.adapt(
            binding.request,
            tenant_id=binding.tenant_id,
            policy_version=binding.policy_version,
        )

    def _route_authorization(
        self,
        binding: WorkflowControlRunBinding,
        *,
        start_request_id: str = "",
    ) -> dict[str, str]:
        envelope = {
            "schema": ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
            "tenant_id": self._principal.tenant_id,
            "subject_id": self._principal.subject_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
        }
        if start_request_id:
            envelope["start_request_id"] = start_request_id
        return envelope

    @staticmethod
    def _not_found(workflow_id: str) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "hub-control",
            "workflow_id": str(workflow_id),
            "status": "not_found",
            "events": [],
        }
