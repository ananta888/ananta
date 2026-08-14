"""Runtime-neutral router for the single Hub workflow control plane."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent.services.workflow_control_bindings import WorkflowControlBindingStore
from agent.services.workflow_control_service import (
    HubWorkflowTaskBridge,
    RuntimeSelection,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan


class WorkflowRuntimeBridgeRegistry(HubWorkflowTaskBridge):
    """Route by immutable Hub binding; bridges never select one another."""

    def __init__(self, bindings: WorkflowControlBindingStore) -> None:
        self._bindings = bindings
        self._bridges: dict[str, HubWorkflowTaskBridge] = {}
        self._canonical: dict[int, str] = {}
        self._frozen = False

    def register(
        self,
        runtime_id: str,
        bridge: HubWorkflowTaskBridge,
        *,
        aliases: Iterable[str] = (),
    ) -> None:
        if self._frozen:
            raise RuntimeError("workflow_runtime_bridge_registry_frozen")
        canonical = _runtime_id(runtime_id)
        names = {canonical, *(_runtime_id(value) for value in aliases)}
        if any(name in self._bridges and self._bridges[name] is not bridge for name in names):
            raise ValueError("workflow_runtime_bridge_already_registered")
        for name in names:
            self._bridges[name] = bridge
        self._canonical[id(bridge)] = canonical

    @property
    def runtime_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._canonical.values())))

    def freeze(self) -> None:
        if not self._bridges:
            raise RuntimeError("workflow_runtime_bridge_registry_empty")
        self._frozen = True

    def start(
        self,
        *,
        principal: WorkflowPrincipal,
        plan: ExecutionPlan,
        run_id: str,
        selection: RuntimeSelection,
        authorization_envelope: dict[str, Any],
    ) -> WorkflowRunHandle:
        bridge = self._require_bridge(selection.runtime_id)
        binding = self._bindings.get(plan.workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        if binding.runtime_id == "pending":
            binding = self._bindings.bind_runtime(
                binding.workflow_id,
                plan_hash=plan.plan_hash,
                runtime_id=selection.runtime_id,
            )
        bound_bridge = self._bridges.get(_runtime_id(binding.runtime_id))
        if bound_bridge is not bridge:
            raise ValueError("workflow_control_runtime_binding_mismatch")
        return bridge.start(
            principal=principal,
            plan=plan,
            run_id=run_id,
            selection=selection,
            authorization_envelope=authorization_envelope,
        )

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]:
        return self._bridge_for_run(run_id).query(principal=principal, run_id=run_id)

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        return self._bridge_for_run(command.run_id).signal(
            principal=principal,
            command=command,
        )

    def cancel(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        return self._bridge_for_run(command.run_id).cancel(
            principal=principal,
            command=command,
        )

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        return self._bridge_for_run(run_id).history(
            principal=principal,
            run_id=run_id,
            after_sequence=after_sequence,
        )

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for bridge in self._unique_bridges():
            reconcile = getattr(bridge, "reconcile_active", None)
            if callable(reconcile):
                reports.append(dict(reconcile(limit=limit)))
        return {
            "runtime_ids": list(self.runtime_ids),
            "processed": sum(int(value.get("processed") or 0) for value in reports),
            "failed": [item for value in reports for item in value.get("failed", [])],
            "reports": reports,
        }

    def retry_command(
        self,
        *,
        binding: Any,
        command_id: str,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Route an idempotent retry to the bridge bound by the Hub."""

        bridge = self._require_bridge(str(binding.runtime_id))
        retry = getattr(bridge, "retry_command", None)
        if not callable(retry):
            return None
        value = retry(
            binding=binding,
            command_id=command_id,
            command_type=command_type,
            payload=payload,
        )
        return dict(value) if value is not None else None

    def recover_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        """Replay one durably admitted synchronous command under Hub control."""

        bridge = self._bridge_for_run(command.run_id)
        recover = getattr(bridge, "recover_command", None)
        if not callable(recover):
            raise RuntimeError("workflow_control_command_recovery_unsupported")
        value = recover(principal=principal, command=command)
        if not isinstance(value, dict):
            raise TypeError("workflow_control_command_recovery_invalid")
        return dict(value)

    def _bridge_for_run(self, run_id: str) -> HubWorkflowTaskBridge:
        binding = self._bindings.get_by_run_id(str(run_id))
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return self._require_bridge(binding.runtime_id)

    def _require_bridge(self, runtime_id: str) -> HubWorkflowTaskBridge:
        bridge = self._bridges.get(_runtime_id(runtime_id))
        if bridge is None:
            raise LookupError("workflow_runtime_bridge_not_registered")
        return bridge

    def _unique_bridges(self) -> tuple[HubWorkflowTaskBridge, ...]:
        values: list[HubWorkflowTaskBridge] = []
        seen: set[int] = set()
        for bridge in self._bridges.values():
            if id(bridge) not in seen:
                seen.add(id(bridge))
                values.append(bridge)
        return tuple(values)


def _runtime_id(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or len(normalized) > 64:
        raise ValueError("workflow_runtime_id_invalid")
    return normalized


__all__ = ["WorkflowRuntimeBridgeRegistry"]
