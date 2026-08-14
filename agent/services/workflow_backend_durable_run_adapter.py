"""Temporal infrastructure adapter behind the Hub workflow-control bridge."""

from __future__ import annotations

from typing import Any

from agent.services.workflow_backend import WorkflowBackend, WorkflowRequest
from agent.services.workflow_control_command_verification import (
    HubVerifiedDurableCommandPort,
)
from agent.services.workflow_runtime.commands import WorkflowCommandIssuer

DURABLE_RUN_START_SCHEMA = "ananta.durable_run_start.v1"
DURABLE_RUN_SIGNAL_SCHEMA = "ananta.durable_run_signal.v1"


class WorkflowBackendDurableRunAdapter:
    """Adapt legacy Temporal calls to the segregated durable-run port.

    The adapter performs no authorization, policy decision or task creation;
    those responsibilities remain in the Hub control service and bridge.
    """

    def __init__(
        self,
        backend: WorkflowBackend,
        *,
        commands: HubVerifiedDurableCommandPort | None = None,
        command_issuer: WorkflowCommandIssuer | None = None,
    ) -> None:
        if str(backend.backend_id) != "temporal":
            raise ValueError("durable_run_backend_must_be_temporal")
        self._backend = backend
        self._commands = commands
        self._command_issuer = command_issuer

    def start(self, command: dict[str, Any]) -> dict[str, Any]:
        if str(command.get("schema") or "") != DURABLE_RUN_START_SCHEMA:
            raise ValueError("durable_run_start_schema_unsupported")
        tenant_id = str(command.get("tenant_id") or "").strip()
        workflow_id = str(command.get("workflow_id") or "").strip()
        run_id = str(command.get("run_id") or "").strip()
        raw_request = command.get("workflow_request")
        if not tenant_id or not workflow_id or not run_id or not isinstance(raw_request, dict):
            raise ValueError("durable_run_start_binding_required")
        request = WorkflowRequest.from_mapping(raw_request)
        if request.workflow_id != workflow_id:
            raise ValueError("durable_run_workflow_binding_mismatch")
        if str(request.metadata.get("tenant_id") or "") != tenant_id:
            raise ValueError("durable_run_tenant_binding_mismatch")
        if str(request.metadata.get("run_id") or "") != run_id:
            raise ValueError("durable_run_id_binding_mismatch")
        return self._mapping(self._backend.start_workflow(request))

    def describe(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        self._require_scope(tenant_id, run_id)
        query = getattr(self._backend, "query_workflow", None)
        if callable(query):
            return self._mapping(query(run_id, "status"))
        return self._mapping(self._backend.get_workflow_status(run_id))

    def signal(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_scope(tenant_id, run_id)
        if str(command.get("schema") or "") != DURABLE_RUN_SIGNAL_SCHEMA:
            raise ValueError("durable_run_signal_schema_unsupported")
        if self._commands is None:
            raise PermissionError("temporal_hub_verified_command_required")
        signed = self._commands.verify(
            tenant_id=tenant_id,
            run_id=run_id,
            command=command,
        )
        update = getattr(self._backend, "update_workflow", None)
        if not callable(update):
            raise RuntimeError("temporal_workflow_update_port_required")
        return self._mapping(update(run_id, signed.to_dict(), update_id=signed.command_id))

    def signal_persisted(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        """Replay one persisted Hub intent with Temporal update idempotency."""

        self._require_scope(tenant_id, run_id)
        if self._commands is None:
            raise PermissionError("temporal_hub_verified_command_required")
        if self._command_issuer is None:
            raise PermissionError("temporal_hub_command_reissuer_required")
        signed = self._commands.verify_persisted(
            tenant_id=tenant_id,
            run_id=run_id,
            command=command,
        )
        update = getattr(self._backend, "update_workflow", None)
        if not callable(update):
            raise RuntimeError("temporal_workflow_update_port_required")
        renewed = self._command_issuer.issue(
            command_id=signed.command_id,
            command_type=signed.command_type,
            tenant_id=signed.tenant_id,
            workflow_id=signed.workflow_id,
            run_id=signed.run_id,
            step_id=signed.step_id,
            checkpoint_id=signed.checkpoint_id,
            expected_revision=signed.expected_revision,
            plan_hash=signed.plan_hash,
            policy_version=signed.policy_version,
            actor_id=signed.actor_id,
            actor_roles=signed.actor_roles,
            payload=dict(signed.payload),
        )
        return self._mapping(update(run_id, renewed.to_dict(), update_id=signed.command_id))

    def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        reason: str,
    ) -> dict[str, Any]:
        self._require_scope(tenant_id, run_id)
        return self._mapping(self._backend.cancel_workflow(run_id, reason=str(reason)[:1000]))

    def history(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_cursor: str = "",
    ) -> dict[str, Any]:
        self._require_scope(tenant_id, run_id)
        try:
            offset = int(after_cursor or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("durable_run_history_cursor_invalid") from exc
        if offset < 0:
            raise ValueError("durable_run_history_cursor_invalid")
        events = self._backend.list_workflow_events(run_id)
        safe_events = [dict(event) for event in events[offset:] if isinstance(event, dict)]
        return {
            "events": safe_events,
            "next_cursor": str(offset + len(safe_events)),
        }

    @staticmethod
    def _require_scope(tenant_id: str, run_id: str) -> None:
        if not str(tenant_id).strip() or not str(run_id).strip():
            raise ValueError("durable_run_scope_required")

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("durable_run_invalid_response")
        return dict(value)


__all__ = [
    "DURABLE_RUN_SIGNAL_SCHEMA",
    "DURABLE_RUN_START_SCHEMA",
    "WorkflowBackendDurableRunAdapter",
]
