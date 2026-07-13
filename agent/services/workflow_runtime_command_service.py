"""Fail-closed Hub command facade for workflow-runtime operations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.common.audit import log_audit
from agent.services.workflow_runtime._serialization import redact_json
from agent.services.workflow_runtime_operations_models import RuntimeGateView
from agent.services.workflow_runtime_read_model_service import (
    WorkflowRuntimeReadModelService,
    get_workflow_runtime_read_model_service,
)

RUNTIME_OPERATIONS_COMMANDS = frozenset(
    {"pause_run", "resume_run", "cancel_run", "retry_run_or_task"}
)


class HubRuntimeCommandGateway(Protocol):
    """Small Hub-owned command port; implementations must not call workers directly."""

    def send(
        self,
        *,
        tenant_id: str,
        command_type: str,
        task_id: str,
        run_id: str,
        requested_by: str,
        idempotency_key: str,
        governance_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class WorkflowControlBindingLookupPort(Protocol):
    def get_by_run_id(self, run_id: str) -> Any | None: ...


class WorkflowRuntimeGatewayError(RuntimeError):
    def __init__(self, reason_code: str, http_status: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = int(http_status)


class RunControlRuntimeCommandGateway:
    """Adapter to Ananta's existing Hub RunControlService."""

    def send(
        self,
        *,
        tenant_id: str,
        command_type: str,
        task_id: str,
        run_id: str,
        requested_by: str,
        idempotency_key: str,
        governance_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del tenant_id
        from agent.services.run_control_service import get_run_control_service

        command = get_run_control_service().send_command(
            command_type=command_type,
            task_id=task_id,
            run_id=run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            payload={"runtime_operations_governance": dict(governance_context)},
        )
        return dict(command.as_dict())


class WorkflowAwareRuntimeCommandGateway:
    """Route bound workflows to WorkflowControl; keep RunControl for tasks."""

    _COMMAND_MAP = {
        "pause_run": "pause",
        "resume_run": "resume",
        "cancel_run": "cancel",
        "retry_run_or_task": "retry",
    }

    def __init__(
        self,
        *,
        bindings: WorkflowControlBindingLookupPort | None = None,
        facade_provider: Callable[[], Any] | None = None,
        task_gateway: HubRuntimeCommandGateway | None = None,
    ) -> None:
        self._bindings = bindings
        self._facade_provider = facade_provider
        self._task_gateway = task_gateway or RunControlRuntimeCommandGateway()

    def send(
        self,
        *,
        tenant_id: str,
        command_type: str,
        task_id: str,
        run_id: str,
        requested_by: str,
        idempotency_key: str,
        governance_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        bindings = self._bindings or _workflow_control_bindings()
        binding = bindings.get_by_run_id(run_id)
        if binding is None:
            return self._task_gateway.send(
                tenant_id=tenant_id,
                command_type=command_type,
                task_id=task_id,
                run_id=run_id,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
                governance_context=governance_context,
            )
        if str(binding.tenant_id) != str(tenant_id):
            raise WorkflowRuntimeGatewayError("runtime_run_not_found", 404)
        if str(binding.subject_id) != str(requested_by):
            raise WorkflowRuntimeGatewayError("runtime_workflow_owner_required", 403)
        canonical = self._COMMAND_MAP.get(command_type)
        if canonical is None:
            raise WorkflowRuntimeGatewayError("runtime_command_type_forbidden", 400)

        from agent.services.workflow_route_authorization_service import WorkflowRoutePrincipal

        facade = (
            self._facade_provider()
            if self._facade_provider is not None
            else _workflow_control_facade()
        )
        registered = set(getattr(facade.registry, "runtime_ids", ()))
        if str(binding.runtime_id) not in registered and str(facade.backend_id) != str(
            binding.runtime_id
        ):
            raise WorkflowRuntimeGatewayError(
                "runtime_workflow_backend_binding_mismatch",
                409,
            )
        controlled = facade.bind(
            WorkflowRoutePrincipal(
                tenant_id=str(binding.tenant_id),
                subject=str(binding.subject_id),
            )
        )
        try:
            status = controlled.command_workflow(
                str(binding.workflow_id),
                command_type=canonical,
                command_id=idempotency_key,
                payload={
                    "runtime_operations_governance": dict(governance_context),
                },
            )
        except PermissionError as exc:
            raise WorkflowRuntimeGatewayError(
                "runtime_workflow_command_denied",
                403,
            ) from exc
        except ValueError as exc:
            raise WorkflowRuntimeGatewayError(
                "runtime_workflow_command_conflict",
                409,
            ) from exc
        except RuntimeError as exc:
            reason = str(exc).lower()
            is_conflict = any(
                marker in reason
                for marker in ("conflict", "mismatch", "revision", "replay")
            )
            raise WorkflowRuntimeGatewayError(
                (
                    "runtime_workflow_command_conflict"
                    if is_conflict
                    else "runtime_workflow_command_unavailable"
                ),
                409 if is_conflict else 503,
            ) from exc
        if str(status.get("status") or "") == "not_found":
            raise WorkflowRuntimeGatewayError("runtime_run_not_found", 404)
        return {
            "command_id": idempotency_key,
            "type": command_type,
            "status": "accepted",
            "run_id": run_id,
            "workflow_id": str(binding.workflow_id),
            "workflow_status": dict(status),
        }


@dataclass(frozen=True)
class RuntimeOperationCommandRequest:
    command_type: str
    approval_id: str
    evidence_refs: tuple[str, ...]
    idempotency_key: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        idempotency_key: str = "",
    ) -> "RuntimeOperationCommandRequest":
        command_type = str(value.get("type") or value.get("command_type") or "").strip()
        approval_id = str(value.get("approval_id") or "").strip()
        refs_raw = value.get("evidence_refs") or ()
        if not isinstance(refs_raw, (list, tuple, set, frozenset)):
            raise RuntimeOperationCommandError("runtime_command_verified_evidence_required", 422)
        refs = tuple(sorted({str(item).strip() for item in refs_raw if str(item).strip()}))
        resolved_key = str(idempotency_key or value.get("idempotency_key") or "").strip()
        if command_type not in RUNTIME_OPERATIONS_COMMANDS:
            raise RuntimeOperationCommandError("runtime_command_type_forbidden", 400)
        if not approval_id:
            raise RuntimeOperationCommandError("runtime_command_approval_required", 422)
        if not refs or len(refs) > 20:
            raise RuntimeOperationCommandError("runtime_command_verified_evidence_required", 422)
        if not (8 <= len(resolved_key) <= 200):
            raise RuntimeOperationCommandError("runtime_command_idempotency_key_required", 400)
        return cls(
            command_type=command_type,
            approval_id=approval_id[:160],
            evidence_refs=refs,
            idempotency_key=resolved_key,
        )


class RuntimeOperationCommandError(RuntimeError):
    def __init__(self, reason_code: str, http_status: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = int(http_status)


class WorkflowRuntimeCommandService:
    """Validates runtime evidence and approvals before dispatching a Hub command."""

    def __init__(
        self,
        *,
        read_models: WorkflowRuntimeReadModelService,
        gateway: HubRuntimeCommandGateway,
    ) -> None:
        self._read_models = read_models
        self._gateway = gateway

    def dispatch(
        self,
        *,
        tenant_id: str,
        run_id: str,
        actor: str,
        request: RuntimeOperationCommandRequest,
        now: float | None = None,
    ) -> dict[str, Any]:
        evaluated_at = float(now if now is not None else time.time())
        record = self._read_models.get_record(tenant_id=tenant_id, run_id=run_id)
        if record is None:
            self._deny(tenant_id, run_id, actor, request, "runtime_run_not_found")
            raise RuntimeOperationCommandError("runtime_run_not_found", 404)
        if record.is_stale(now=evaluated_at):
            self._deny(tenant_id, run_id, actor, request, "runtime_read_model_stale")
            raise RuntimeOperationCommandError("runtime_read_model_stale", 409)
        if not record.task_id:
            self._deny(tenant_id, run_id, actor, request, "runtime_hub_task_binding_missing")
            raise RuntimeOperationCommandError("runtime_hub_task_binding_missing", 422)

        verified_refs = {item.evidence_id for item in record.verified_evidence}
        if not set(request.evidence_refs).issubset(verified_refs):
            self._deny(tenant_id, run_id, actor, request, "runtime_command_evidence_unverified")
            raise RuntimeOperationCommandError("runtime_command_evidence_unverified", 422)

        try:
            gate = self._approved_gate(
                record.gates,
                request=request,
                now=evaluated_at,
            )
        except RuntimeOperationCommandError as exc:
            self._deny(tenant_id, run_id, actor, request, exc.reason_code)
            raise
        governance_context = {
            "approval_id": gate.approval_id,
            "gate_id": gate.gate_id,
            "evidence_refs": list(request.evidence_refs),
            "read_model_sequence": record.source_sequence,
        }
        namespaced_key = f"runtime-ops:{tenant_id}:{run_id}:{request.idempotency_key}"
        try:
            command = dict(
                self._gateway.send(
                    tenant_id=tenant_id,
                    command_type=request.command_type,
                    task_id=record.task_id,
                    run_id=record.run_id,
                    requested_by=actor,
                    idempotency_key=namespaced_key,
                    governance_context=governance_context,
                )
            )
        except WorkflowRuntimeGatewayError as exc:
            self._deny(tenant_id, run_id, actor, request, exc.reason_code)
            raise RuntimeOperationCommandError(
                exc.reason_code,
                exc.http_status,
            ) from exc

        log_audit(
            "workflow_runtime_operations_command_submitted",
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "task_id": record.task_id,
                "actor": actor,
                "command_type": request.command_type,
                "approval_id": gate.approval_id,
                "evidence_count": len(request.evidence_refs),
                "command_id": command.get("command_id"),
                "command_status": command.get("status"),
            },
        )
        return dict(redact_json(command))

    @staticmethod
    def _approved_gate(
        gates: tuple[RuntimeGateView, ...],
        *,
        request: RuntimeOperationCommandRequest,
        now: float,
    ) -> RuntimeGateView:
        matching = [gate for gate in gates if gate.approval_id == request.approval_id]
        if not matching:
            raise RuntimeOperationCommandError("runtime_command_approval_binding_invalid", 422)
        gate = matching[0]
        if gate.status != "approved":
            raise RuntimeOperationCommandError("runtime_command_approval_not_granted", 422)
        if gate.expires_at is not None and gate.expires_at <= now:
            raise RuntimeOperationCommandError("runtime_command_approval_expired", 422)
        if gate.allowed_commands and request.command_type not in gate.allowed_commands:
            raise RuntimeOperationCommandError("runtime_command_approval_action_mismatch", 422)
        if not set(gate.required_evidence_refs).issubset(set(request.evidence_refs)):
            raise RuntimeOperationCommandError("runtime_command_approval_evidence_mismatch", 422)
        return gate

    @staticmethod
    def _deny(
        tenant_id: str,
        run_id: str,
        actor: str,
        request: RuntimeOperationCommandRequest,
        reason_code: str,
    ) -> None:
        log_audit(
            "workflow_runtime_operations_command_denied",
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "actor": actor,
                "command_type": request.command_type,
                "approval_id": request.approval_id,
                "evidence_count": len(request.evidence_refs),
                "reason_code": reason_code,
            },
        )


_command_service = WorkflowRuntimeCommandService(
    read_models=get_workflow_runtime_read_model_service(),
    gateway=WorkflowAwareRuntimeCommandGateway(),
)


def get_workflow_runtime_command_service() -> WorkflowRuntimeCommandService:
    return _command_service


def _workflow_control_bindings() -> WorkflowControlBindingLookupPort:
    from agent.database import engine
    from agent.services.workflow_control_persistence import (
        SQLAlchemyWorkflowControlBindingStore,
    )

    return SQLAlchemyWorkflowControlBindingStore(engine)


def _workflow_control_facade() -> Any:
    from agent.services.workflow_control_composition import (
        get_workflow_backend_control_facade,
    )

    return get_workflow_backend_control_facade()


__all__ = [
    "HubRuntimeCommandGateway",
    "RUNTIME_OPERATIONS_COMMANDS",
    "RunControlRuntimeCommandGateway",
    "RuntimeOperationCommandError",
    "RuntimeOperationCommandRequest",
    "WorkflowRuntimeCommandService",
    "WorkflowAwareRuntimeCommandGateway",
    "WorkflowRuntimeGatewayError",
    "get_workflow_runtime_command_service",
]
