"""Facade exposing the composed workflow backend control surface and binding request principals."""

from __future__ import annotations

import uuid
from typing import Any

from agent.services.workflow_authorized_backend import AuthorizedWorkflowBackend
from agent.services.workflow_configured_backend_bridge import ConfiguredWorkflowBackendBridge
from agent.services.workflow_control_bindings import WorkflowControlBindingStore, WorkflowControlRunBinding
from agent.services.workflow_control_command_guards import _authoritative_projection_binding, _canonical_public_status
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptReconciler,
    WorkflowControlCommandReceiptStore,
    admitted_receipt_command,
)
from agent.services.workflow_control_service import WorkflowControlService, WorkflowPrincipal
from agent.services.workflow_route_authorization_service import WorkflowRoutePrincipal
from agent.services.workflow_runtime_bridge_registry import WorkflowRuntimeBridgeRegistry
from agent.services.workflow_terminal_trace_reconciliation import WorkflowTerminalTraceReconciler
from agent.services.workflow_transition_native_composition import WorkflowCommandTransitionRuntime


class WorkflowBackendControlFacade:
    """Bind legacy backend-shaped callers to one Hub control service."""

    def __init__(
        self,
        *,
        control: WorkflowControlService,
        bridge: ConfiguredWorkflowBackendBridge,
        bindings: WorkflowControlBindingStore,
        registry: WorkflowRuntimeBridgeRegistry,
        command_receipts: WorkflowControlCommandReceiptStore,
        transitions: WorkflowCommandTransitionRuntime | None = None,
        trace_reconciler: WorkflowTerminalTraceReconciler | None = None,
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._registry = registry
        self._command_receipts = command_receipts
        self._transitions = transitions
        self._trace_reconciler = trace_reconciler
        receipt_reconciler_owner = f"receipt-reconciler:{uuid.uuid4().hex}"
        self._receipt_reconciler = WorkflowControlCommandReceiptReconciler(
            receipts=command_receipts,
            bindings=bindings,
            project=self._project_public_status,
            recover=self._recover_command_receipt_runtime,
            owner_id=receipt_reconciler_owner,
        )

    def _project_public_status(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        binding = _authoritative_projection_binding(self._bindings, binding)
        previous = self._bindings.last_public_status(binding.workflow_id)
        projected = _canonical_public_status(
            binding,
            status,
            previous=previous,
        )
        self._bindings.record_public_status(binding.workflow_id, projected)
        persisted = self._bindings.last_public_status(binding.workflow_id)
        if persisted is None:
            raise RuntimeError("workflow_control_public_status_missing")
        return persisted

    def _recover_command_receipt_runtime(
        self,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> dict[str, Any]:
        command = admitted_receipt_command(receipt)
        return dict(
            self._registry.recover_command(
                principal=WorkflowPrincipal(
                    tenant_id=receipt.tenant_id,
                    subject_id=receipt.actor_id,
                    roles=command.actor_roles,
                ),
                command=command,
            )
        )

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def bind(self, principal: WorkflowRoutePrincipal) -> "AuthorizedWorkflowBackend":
        return AuthorizedWorkflowBackend(
            control=self._control,
            bridge=self._bridge,
            bindings=self._bindings,
            command_receipts=self._command_receipts,
            receipt_reconciler=self._receipt_reconciler,
            registry=self._registry,
            project_public_status=self._project_public_status,
            transitions=self._transitions,
            principal=WorkflowPrincipal(
                tenant_id=principal.tenant_id,
                subject_id=principal.subject,
                roles=principal.roles,
            ),
        )

    @property
    def control_service(self) -> WorkflowControlService:
        return self._control

    @property
    def bindings(self) -> WorkflowControlBindingStore:
        return self._bindings

    @property
    def registry(self) -> WorkflowRuntimeBridgeRegistry:
        return self._registry

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        """Advance active runs only from the Hub background reconciliation path."""

        # Transitions are driven before receipts so a transition that finalizes
        # here is already terminal when the receipt reconciler reads it, rather
        # than being observed mid-flight and deferred a whole cycle.
        transitions = self._transitions.driver.tick() if self._transitions is not None else None
        receipts = self._receipt_reconciler.drain(limit=limit)
        runtime = dict(self._registry.reconcile_active(limit=limit))
        # Traces are drained last: a run that finalized earlier in this same
        # pass is already terminal here, so its trace is projected without
        # waiting a whole cycle.
        traces = self._trace_reconciler.drain(limit=limit) if self._trace_reconciler is not None else None
        driven = transitions.processed if transitions is not None else 0
        projected = traces.projected if traces is not None else 0
        if not receipts["processed"] and not receipts["failed"] and not driven and not projected:
            return runtime
        reports: list[dict[str, Any]] = [receipts, runtime]
        if transitions is not None:
            reports.insert(0, transitions.to_dict())
        if traces is not None:
            reports.append(traces.to_dict())
        return {
            **runtime,
            "processed": int(runtime.get("processed") or 0) + int(receipts["processed"]) + driven + projected,
            "failed": [*list(runtime.get("failed") or ()), *receipts["failed"], *(traces.failed if traces else ())],
            "reports": reports,
        }
