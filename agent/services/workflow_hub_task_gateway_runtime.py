"""Production composition root for the internal workflow task gateway."""

from __future__ import annotations

import json
import os
import threading
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
from agent.services.workflow_runtime.security import SignatureSigningKeyRingPort
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
    RuntimeAuthorizationCryptoError,
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
_AUTH_KEY_RING: SignatureSigningKeyRingPort | None = None
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


def get_workflow_authorization_key_ring() -> SignatureSigningKeyRingPort:
    """Return the Hub-only signer; production never accepts shared HMAC."""

    global _AUTH_KEY_RING
    if _AUTH_KEY_RING is not None:
        return _AUTH_KEY_RING
    with _SERVICE_LOCK:
        if _AUTH_KEY_RING is None:
            signing_path = str(os.environ.get("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE") or "").strip()
            if signing_path:
                auth_config = _read_json_file(
                    "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE",
                    label="workflow authorization signing keyring",
                )
                try:
                    _AUTH_KEY_RING = Ed25519SigningKeyRing.from_mapping(auth_config)
                except RuntimeAuthorizationCryptoError as exc:
                    raise WorkflowHubTaskConfigurationError(exc.reason_code) from exc
            elif _legacy_hmac_allowed():
                auth_config = _read_keyring_file(
                    "ANANTA_WORKFLOW_AUTH_KEYRING_FILE",
                    label="legacy workflow authorization keyring",
                )
                legacy = HmacKeyRing(
                    auth_config["keys"],
                    active_key_id=str(auth_config["active_key_id"]),
                )
                for key_id in auth_config.get("revoked_key_ids", ()):
                    legacy.revoke_key(str(key_id))
                for contract_id in auth_config.get("revoked_envelope_ids", ()):
                    legacy.revoke_contract(str(contract_id))
                _AUTH_KEY_RING = legacy
            else:
                raise WorkflowHubTaskConfigurationError("workflow_authorization_ed25519_signing_keyring_required")
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
    decoded = _read_json_file(environment_name, label=label)
    keys = decoded.get("keys")
    active_key_id = str(decoded.get("active_key_id") or "")
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
            or any(not isinstance(value, str) or not value or len(value) > 256 for value in values)
        ):
            raise WorkflowHubTaskConfigurationError(f"{label} {field_name} is invalid")
        result[field_name] = list(dict.fromkeys(values))
    return result


def _read_json_file(environment_name: str, *, label: str) -> dict[str, Any]:
    raw_path = str(os.environ.get(environment_name) or "").strip()
    if not raw_path:
        raise WorkflowHubTaskConfigurationError(f"{label} file reference is required and must be absolute")
    try:
        raw = read_file_managed_bytes(
            raw_path,
            description=f"{label} file",
            max_bytes=65_536,
        )
    except FileCredentialConfigurationError as exc:
        reason = str(exc)
        if "size is invalid" in reason:
            message = f"{label} file is invalid"
        elif "cannot be opened securely" in reason or "cannot be read securely" in reason:
            message = f"{label} file cannot be read"
        else:
            message = f"{label} file is unsafe"
        raise WorkflowHubTaskConfigurationError(message) from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowHubTaskConfigurationError(f"{label} file is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise WorkflowHubTaskConfigurationError(f"{label} file must be an object")
    return {str(key): value for key, value in decoded.items()}


def _legacy_hmac_allowed() -> bool:
    raw = str(os.environ.get("ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING") or "").strip().lower()
    if not raw:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise WorkflowHubTaskConfigurationError("ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING must be boolean")
