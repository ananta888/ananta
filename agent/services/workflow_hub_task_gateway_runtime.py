"""Production composition root for the internal workflow task gateway."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from agent.services.workflow_execution_ownership_service import (
    WorkflowExecutionOwnershipService,
)
from agent.services.workflow_hub_task_gateway_service import (
    FernetDispatchPayloadCodec,
    HubTaskRepositoryPort,
    WorkflowHubTaskGatewayService,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    SQLAlchemyEventStore,
    SQLAlchemyExecutionOwnershipStore,
    SQLAlchemySideEffectLedger,
)
from ananta_contracts.temporal_workflow import StepActivityInput


class WorkflowHubTaskConfigurationError(RuntimeError):
    pass


class TaskQueueHubTaskRepository(HubTaskRepositoryPort):
    def get(self, task_id: str) -> dict[str, Any] | None:
        from agent.repository import task_repo

        task = task_repo.get_by_id(str(task_id))
        return task.model_dump() if task is not None else None

    def create(
        self,
        *,
        task_id: str,
        request: StepActivityInput,
        runtime_context: dict[str, Any],
    ) -> None:
        # Import the legacy queue facade lazily: importing ``agent.routes.tasks``
        # eagerly from a composition root creates a service/route cycle.
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="created",
            title=f"Workflow step: {request.task_kind}"[:200],
            description="Hub-authorized workflow runtime step.",
            priority="medium",
            created_by="workflow-control-service",
            source="workflow_runtime",
            tags=["workflow_runtime", "hub_delegated"],
            event_type="workflow_step_delegated",
            event_channel="hub_task_queue",
            event_details={
                "workflow_id": request.workflow_id,
                "run_id": request.run_id,
                "step_id": request.step_id,
                "operation_id": request.operation_id,
            },
            extra_fields={
                "task_kind": request.task_kind,
                "plan_id": request.plan_hash,
                "plan_node_id": request.step_id,
                "required_capabilities": list(request.required_capabilities),
                "worker_execution_context": {"workflow_runtime": runtime_context},
                "verification_spec": {
                    "schema": "ananta.delegated_execution_result.v1",
                    "artifact_first": True,
                },
            },
        )

    def update(
        self,
        *,
        task_id: str,
        status: str,
        reason_code: str = "",
        verification_status: dict[str, Any] | None = None,
    ) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        persisted_status = "failed" if status == "uncertain" else status
        update_fields: dict[str, Any] = {
            "status_reason_code": reason_code or None,
        }
        if verification_status is not None:
            update_fields["verification_status"] = dict(verification_status)
        update_local_task_status(
            task_id,
            persisted_status,
            event_type=f"workflow_step_{status}",
            event_actor="workflow-control-service",
            event_details={"reason_code": reason_code, "reported_status": status},
            **update_fields,
        )


_SERVICE: WorkflowHubTaskGatewayService | None = None
_AUTH_KEY_RING: HmacKeyRing | None = None
_SERVICE_LOCK = threading.RLock()


def get_workflow_hub_task_gateway_service() -> WorkflowHubTaskGatewayService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = _build_service()
    return _SERVICE


def reset_workflow_hub_task_gateway_service() -> None:
    global _AUTH_KEY_RING, _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
        _AUTH_KEY_RING = None


def get_workflow_authorization_key_ring() -> HmacKeyRing:
    """Return the process-wide Hub signer used by every workflow gateway."""

    global _AUTH_KEY_RING
    if _AUTH_KEY_RING is not None:
        return _AUTH_KEY_RING
    with _SERVICE_LOCK:
        if _AUTH_KEY_RING is None:
            auth_config = _read_keyring_file(
                "ANANTA_WORKFLOW_AUTH_KEYRING_FILE",
                label="workflow authorization keyring",
            )
            _AUTH_KEY_RING = HmacKeyRing(
                auth_config["keys"],
                active_key_id=str(auth_config["active_key_id"]),
            )
            for key_id in auth_config.get("revoked_key_ids", ()):
                _AUTH_KEY_RING.revoke_key(str(key_id))
            for contract_id in auth_config.get("revoked_envelope_ids", ()):
                _AUTH_KEY_RING.revoke_contract(str(contract_id))
    return _AUTH_KEY_RING


def _build_service() -> WorkflowHubTaskGatewayService:
    from agent.database import engine
    from agent.services.workflow_authorization_grant_service import (
        SQLAlchemyWorkflowAuthorizationGrantService,
    )
    from agent.services.workflow_control_persistence import (
        SQLAlchemyWorkflowCommandReplayNonceStore,
    )
    from agent.services.workflow_runtime.telemetry_runtime import configure_workflow_telemetry

    dispatch_config = _read_keyring_file(
        "ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE",
        label="workflow dispatch encryption keyring",
    )
    key_ring = get_workflow_authorization_key_ring()
    events = configure_workflow_telemetry(SQLAlchemyEventStore(engine))
    return WorkflowHubTaskGatewayService(
        tasks=TaskQueueHubTaskRepository(),
        authorization=AuthorizationVerifier(
            key_ring,
            SQLAlchemyWorkflowCommandReplayNonceStore(engine),
        ),
        ledger=SQLAlchemySideEffectLedger(engine),
        ownership=WorkflowExecutionOwnershipService(
            SQLAlchemyExecutionOwnershipStore(engine),
            events,
        ),
        events=events,
        codec=FernetDispatchPayloadCodec(
            dispatch_config["keys"],
            active_key_id=str(dispatch_config["active_key_id"]),
        ),
        authorization_revalidator=SQLAlchemyWorkflowAuthorizationGrantService(engine),
    )


def _read_keyring_file(environment_name: str, *, label: str) -> dict[str, Any]:
    raw_path = str(os.environ.get(environment_name) or "").strip()
    path = Path(raw_path)
    if not raw_path or not path.is_absolute():
        raise WorkflowHubTaskConfigurationError(f"{label} file reference is required and must be absolute")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkflowHubTaskConfigurationError(f"{label} file cannot be read") from exc
    if not raw or len(raw) > 65_536:
        raise WorkflowHubTaskConfigurationError(f"{label} file is invalid")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowHubTaskConfigurationError(f"{label} file is not valid JSON") from exc
    keys = decoded.get("keys") if isinstance(decoded, dict) else None
    active_key_id = str(decoded.get("active_key_id") or "") if isinstance(decoded, dict) else ""
    if not isinstance(keys, dict) or active_key_id not in keys:
        raise WorkflowHubTaskConfigurationError(f"{label} file is incomplete")
    result: dict[str, Any] = {
        "active_key_id": active_key_id,
        "keys": dict(keys),
    }
    for field_name in ("revoked_key_ids", "revoked_envelope_ids"):
        values = decoded.get(field_name)
        if values is None:
            result[field_name] = []
            continue
        if (
            not isinstance(values, list)
            or len(values) > 10_000
            or any(
                not isinstance(value, str)
                or not value
                or len(value) > 256
                for value in values
            )
        ):
            raise WorkflowHubTaskConfigurationError(
                f"{label} {field_name} is invalid"
            )
        result[field_name] = list(dict.fromkeys(values))
    return result
