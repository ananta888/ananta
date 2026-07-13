"""Hub-owned, encrypted task bridge used by durable runtime Activities."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.fernet import Fernet, InvalidToken

from agent.services.workflow_authorization_grant_service import (
    HubAuthorizationRevalidationPort,
    UnavailableHubAuthorizationRevalidator,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    CanonicalWorkflowEvent,
    EventStore,
    ExecutionOwnershipStore,
    RuntimeAuthorizationEnvelope,
    SideEffectLedger,
    operation_id_for,
    side_effect_event,
)
from agent.services.workflow_runtime.errors import InvalidTransitionError
from ananta_contracts.hub_task_gateway import (
    HUB_TASK_COMMAND_SCHEMA,
    HUB_TASK_RECEIPT_SCHEMA,
    RETRY_BUDGET_RECEIPT_SCHEMA,
    RETRY_CATEGORIES,
)
from ananta_contracts.temporal_workflow import (
    ACTIVITY_INPUT_SCHEMA,
    StepActivityInput,
    TemporalContractError,
)


class WorkflowHubTaskError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class HubTaskRepositoryPort(Protocol):
    def get(self, task_id: str) -> dict[str, Any] | None: ...

    def create(self, *, task_id: str, request: StepActivityInput, runtime_context: dict[str, Any]) -> None: ...

    def update(
        self,
        *,
        task_id: str,
        status: str,
        reason_code: str = "",
        verification_status: dict[str, Any] | None = None,
    ) -> None: ...


class DispatchPayloadCodec(Protocol):
    def seal(self, payload: dict[str, Any]) -> dict[str, str]: ...

    def open(self, sealed: Mapping[str, Any]) -> dict[str, Any]: ...


class FernetDispatchPayloadCodec:
    """Encrypt authorization-bearing dispatch payloads before task persistence."""

    def __init__(self, keys: Mapping[str, str | bytes], *, active_key_id: str) -> None:
        self._keys: dict[str, Fernet] = {}
        for key_id, raw_key in keys.items():
            encoded = raw_key.encode("ascii") if isinstance(raw_key, str) else bytes(raw_key)
            try:
                self._keys[str(key_id)] = Fernet(encoded)
            except (ValueError, TypeError) as exc:
                raise ValueError("dispatch_encryption_key_invalid") from exc
        if active_key_id not in self._keys:
            raise ValueError("dispatch_active_key_missing")
        self._active_key_id = str(active_key_id)

    def seal(self, payload: dict[str, Any]) -> dict[str, str]:
        rendered = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(rendered) > 262_144:
            raise WorkflowHubTaskError("workflow_dispatch_payload_too_large", status_code=413)
        return {
            "schema": "ananta.encrypted_workflow_dispatch.v1",
            "key_id": self._active_key_id,
            "ciphertext": self._keys[self._active_key_id].encrypt(rendered).decode("ascii"),
            "payload_digest": hashlib.sha256(rendered).hexdigest(),
        }

    def open(self, sealed: Mapping[str, Any]) -> dict[str, Any]:
        key_id = str(sealed.get("key_id") or "")
        cipher = self._keys.get(key_id)
        if cipher is None:
            raise WorkflowHubTaskError("workflow_dispatch_key_unknown", status_code=503)
        try:
            raw = cipher.decrypt(str(sealed.get("ciphertext") or "").encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise WorkflowHubTaskError("workflow_dispatch_decryption_failed", status_code=409) from exc
        if not isinstance(payload, dict):
            raise WorkflowHubTaskError("workflow_dispatch_payload_invalid", status_code=409)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(sealed.get("payload_digest") or ""):
            raise WorkflowHubTaskError("workflow_dispatch_digest_mismatch", status_code=409)
        return payload


@dataclass(frozen=True)
class WorkflowHubTaskGatewayConfig:
    owner_id: str = "temporal-activity-gateway"
    lease_seconds: float = 1_800.0
    maximum_payload_bytes: int = 262_144


class WorkflowHubTaskGatewayService:
    """Revalidate authority, ledger intent, and enqueue through the Hub queue."""

    def __init__(
        self,
        *,
        tasks: HubTaskRepositoryPort,
        authorization: AuthorizationVerifier,
        ledger: SideEffectLedger,
        ownership: ExecutionOwnershipStore,
        events: EventStore,
        codec: DispatchPayloadCodec,
        authorization_revalidator: HubAuthorizationRevalidationPort | None = None,
        config: WorkflowHubTaskGatewayConfig | None = None,
    ) -> None:
        self._tasks = tasks
        self._authorization = authorization
        self._ledger = ledger
        self._ownership = ownership
        self._events = events
        self._codec = codec
        self._authorization_revalidator = (
            authorization_revalidator or UnavailableHubAuthorizationRevalidator()
        )
        self._config = config or WorkflowHubTaskGatewayConfig()

    def submit(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if str(raw.get("schema") or "") != HUB_TASK_COMMAND_SCHEMA or raw.get("command") != "submit":
            raise WorkflowHubTaskError("workflow_hub_task_command_invalid", status_code=400)
        request = self._activity_request(raw)
        self._validate_operation_binding(request)

        hub_task_id = self._task_id(request.operation_id)
        sealed = self._codec.seal(request.to_dict())
        existing = self._tasks.get(hub_task_id)
        runtime_envelope = RuntimeAuthorizationEnvelope.from_mapping(request.authorization_envelope.to_dict())
        self._verify_authorization(request, runtime_envelope, consume_nonce=existing is None)
        if existing is not None:
            context = self._runtime_context(existing)
            if context.get("payload_digest") != sealed["payload_digest"]:
                raise WorkflowHubTaskError("workflow_dispatch_dedupe_conflict")
            return self._receipt(existing, operation_id=request.operation_id)

        claim = self._ownership.claim(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            step_id=request.step_id,
            owner_id=self._config.owner_id,
            lease_seconds=self._config.lease_seconds,
            maximum_retries=int(request.retry_budget_maximum or 0),
        )
        if not claim.acquired:
            raise WorkflowHubTaskError(f"workflow_step_claim_denied:{claim.reason}")
        side_effect_class = self._side_effect_class(request)
        declared_operation = str(request.parameters.get("declared_operation") or "hub_task")
        planned = self._ledger.plan(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            step_id=request.step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        if planned.operation_id != request.operation_id:
            raise WorkflowHubTaskError("workflow_ledger_operation_binding_mismatch")
        if planned.status == "planned":
            planned = self._ledger.authorize(
                planned.operation_id,
                expected_revision=planned.revision,
                fencing_token=claim.ownership.fencing_token,
                authorization_envelope_id=runtime_envelope.envelope_id,
            )

        event_refs = self._record_delegation_events(request, planned)
        runtime_context = {
            "schema": "ananta.workflow_hub_task_context.v1",
            "tenant_scope_hash": hashlib.sha256(request.tenant_id.encode("utf-8")).hexdigest(),
            "workflow_id": request.workflow_id,
            "run_id": request.run_id,
            "step_id": request.step_id,
            "operation_id": request.operation_id,
            "plan_hash": request.plan_hash,
            "authorization_envelope_id": runtime_envelope.envelope_id,
            "authorization_state": "valid",
            "payload_digest": sealed["payload_digest"],
            "dispatch": sealed,
            "owner_id": claim.ownership.owner_id,
            "attempt_id": claim.ownership.attempt_id,
            "fencing_token": claim.ownership.fencing_token,
            "ownership_revision": claim.ownership.revision,
            "canonical_event_refs": list(event_refs),
        }
        self._tasks.create(
            task_id=hub_task_id,
            request=request,
            runtime_context=runtime_context,
        )
        created = self._tasks.get(hub_task_id)
        if created is None:
            raise WorkflowHubTaskError("workflow_hub_task_persistence_failed", status_code=503)
        return self._receipt(created, operation_id=request.operation_id)

    def consume_retry(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically consume one retry from the single Hub-owned run budget.

        The caller transports the complete signed Activity binding again. This
        lets the Hub authorize a retry even when the first attempt failed before
        a task record was created, while keeping the maximum bound to the signed
        authorization envelope rather than caller-controlled route state.
        """

        if (
            str(raw.get("schema") or "") != HUB_TASK_COMMAND_SCHEMA
            or raw.get("command") != "consume_retry"
        ):
            raise WorkflowHubTaskError("workflow_hub_task_command_invalid", status_code=400)
        request = self._activity_request(raw)
        self._validate_operation_binding(request)
        category = str(raw.get("retry_category") or "")
        retry_id = str(raw.get("retry_id") or "")
        if category not in RETRY_CATEGORIES:
            raise WorkflowHubTaskError("workflow_retry_category_invalid", status_code=422)
        if not retry_id or len(retry_id) > 256 or "\x00" in retry_id:
            raise WorkflowHubTaskError("workflow_retry_id_invalid", status_code=422)

        envelope = RuntimeAuthorizationEnvelope.from_mapping(request.authorization_envelope.to_dict())
        self._verify_authorization(request, envelope, consume_nonce=False)
        try:
            snapshot = self._ownership.consume_retry(
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                retry_id=retry_id,
                category=category,
                maximum=int(request.retry_budget_maximum or 0),
            )
        except InvalidTransitionError as exc:
            reason_code = str(exc) or "retry_budget_denied"
            raise WorkflowHubTaskError(reason_code, status_code=409) from exc
        except ValueError as exc:
            raise WorkflowHubTaskError("retry_budget_input_invalid", status_code=422) from exc

        self._record_retry_event(
            request,
            retry_id=retry_id,
            category=category,
            used=snapshot.used,
            maximum=snapshot.maximum,
        )
        return {
            "schema": RETRY_BUDGET_RECEIPT_SCHEMA,
            "retry_id": retry_id,
            "category": category,
            "used": snapshot.used,
            "maximum": snapshot.maximum,
            "remaining": snapshot.remaining,
        }

    def get(self, *, hub_task_id: str, operation_id: str) -> dict[str, Any]:
        task = self._tasks.get(hub_task_id)
        if task is None:
            raise WorkflowHubTaskError("workflow_hub_task_not_found", status_code=404)
        context = self._runtime_context(task)
        if context.get("operation_id") != operation_id:
            raise WorkflowHubTaskError("workflow_hub_task_operation_mismatch", status_code=404)
        return self._receipt(task, operation_id=operation_id)

    def dispatch_payload(self, *, hub_task_id: str, operation_id: str) -> dict[str, Any]:
        task = self._tasks.get(hub_task_id)
        if task is None:
            raise WorkflowHubTaskError("workflow_hub_task_not_found", status_code=404)
        context = self._runtime_context(task)
        if context.get("operation_id") != operation_id:
            raise WorkflowHubTaskError("workflow_hub_task_operation_mismatch", status_code=404)
        return self._codec.open(dict(context.get("dispatch") or {}))

    def finish(
        self,
        *,
        hub_task_id: str,
        command: Mapping[str, Any],
    ) -> dict[str, Any]:
        task = self._tasks.get(hub_task_id)
        if task is None:
            raise WorkflowHubTaskError("workflow_hub_task_not_found", status_code=404)
        context = self._runtime_context(task)
        operation_id = str(command.get("operation_id") or "")
        if context.get("operation_id") != operation_id:
            raise WorkflowHubTaskError("workflow_hub_task_operation_mismatch", status_code=404)
        status = str(command.get("status") or "")
        if status not in {"completed", "failed", "uncertain"}:
            raise WorkflowHubTaskError("workflow_hub_task_result_status_invalid", status_code=422)
        attempt_id = str(command.get("attempt_id") or "")
        fencing_token = int(command.get("fencing_token") or 0)
        if attempt_id != context.get("attempt_id") or fencing_token != int(context.get("fencing_token") or 0):
            raise WorkflowHubTaskError("workflow_hub_task_fencing_mismatch")
        tenant_id = self._dispatch_tenant(task)
        ledger_record = self._ledger.get(tenant_id=tenant_id, operation_id=operation_id)
        if ledger_record is None:
            raise WorkflowHubTaskError("workflow_ledger_record_missing")
        if ledger_record.status == "authorized":
            claimed = self._ledger.claim(
                operation_id,
                expected_revision=ledger_record.revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
            )
            ledger_record = claimed.record
        if ledger_record.status == "started":
            if status == "completed":
                ledger_record = self._ledger.complete(
                    operation_id,
                    expected_revision=ledger_record.revision,
                    fencing_token=fencing_token,
                    attempt_id=attempt_id,
                    result_ref=str(command.get("result_ref") or hub_task_id),
                )
            elif status == "failed":
                ledger_record = self._ledger.fail(
                    operation_id,
                    expected_revision=ledger_record.revision,
                    fencing_token=fencing_token,
                    attempt_id=attempt_id,
                    failure_code=str(command.get("reason_code") or "worker_execution_failed"),
                )
            else:
                ledger_record = self._ledger.mark_uncertain(
                    operation_id,
                    expected_revision=ledger_record.revision,
                    fencing_token=fencing_token,
                    attempt_id=attempt_id,
                    failure_code=str(command.get("reason_code") or "worker_outcome_unknown"),
                )
        verification = {
            "workflow_runtime": {
                "artifact_refs": list(command.get("artifact_refs") or []),
                "result_ref": str(command.get("result_ref") or ""),
                "ledger_state": ledger_record.status,
            }
        }
        self._tasks.update(
            task_id=hub_task_id,
            status=status,
            reason_code=str(command.get("reason_code") or ""),
            verification_status=verification,
        )
        updated = self._tasks.get(hub_task_id) or task
        return self._receipt(updated, operation_id=operation_id)

    def cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> dict[str, Any]:
        task = self._tasks.get(hub_task_id)
        if task is None:
            raise WorkflowHubTaskError("workflow_hub_task_not_found", status_code=404)
        context = self._runtime_context(task)
        if context.get("operation_id") != operation_id:
            raise WorkflowHubTaskError("workflow_hub_task_operation_mismatch", status_code=404)
        tenant_id = self._dispatch_tenant(task)
        record = self._ledger.get(tenant_id=tenant_id, operation_id=operation_id)
        if record and record.status == "authorized":
            claimed = self._ledger.claim(
                operation_id,
                expected_revision=record.revision,
                fencing_token=int(context.get("fencing_token") or 0),
                attempt_id=str(context.get("attempt_id") or ""),
            )
            record = self._ledger.fail(
                operation_id,
                expected_revision=claimed.record.revision,
                fencing_token=int(context.get("fencing_token") or 0),
                attempt_id=str(context.get("attempt_id") or ""),
                failure_code=str(reason or "workflow_cancelled")[:256],
            )
        self._tasks.update(
            task_id=hub_task_id,
            status="cancelled",
            reason_code=str(reason or "workflow_cancelled")[:256],
        )
        return self._receipt(self._tasks.get(hub_task_id) or task, operation_id=operation_id)

    def _verify_authorization(
        self,
        request: StepActivityInput,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        consume_nonce: bool,
    ) -> None:
        self._authorization.authorize(
            envelope,
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            step_id=request.step_id,
            plan_hash=request.plan_hash,
            policy_version=envelope.policy_version,
            requested_budget={"retries": int(request.retry_budget_maximum or 0)},
            consume_nonce=consume_nonce,
            writing=request.activity_class.value != "read_only",
            hub_revalidator=self._authorization_revalidator.revalidate,
        )

    @staticmethod
    def _activity_request(raw: Mapping[str, Any]) -> StepActivityInput:
        try:
            activity_payload = dict(raw)
            activity_payload["schema"] = ACTIVITY_INPUT_SCHEMA
            activity_payload.pop("command", None)
            activity_payload.pop("retry_id", None)
            activity_payload.pop("retry_category", None)
            return StepActivityInput.from_mapping(activity_payload)
        except TemporalContractError as exc:
            raise WorkflowHubTaskError(exc.reason_code, status_code=422) from exc

    @staticmethod
    def _validate_operation_binding(request: StepActivityInput) -> None:
        declared_operation = str(request.parameters.get("declared_operation") or "hub_task")
        expected_operation_id = operation_id_for(
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            step_id=request.step_id,
            declared_operation=declared_operation,
        )
        if request.operation_id != expected_operation_id:
            raise WorkflowHubTaskError("workflow_operation_id_mismatch", status_code=422)

    @staticmethod
    def _side_effect_class(request: StepActivityInput) -> str:
        explicit = str(request.parameters.get("side_effect_class") or "")
        if explicit in {"read", "idempotent_write", "non_idempotent_write"}:
            return explicit
        return {
            "read_only": "read",
            "idempotent": "idempotent_write",
            "non_idempotent": "non_idempotent_write",
            "long_running": "idempotent_write",
        }[request.activity_class.value]

    def _record_delegation_events(
        self,
        request: StepActivityInput,
        ledger_record: Any,
    ) -> tuple[str, ...]:
        current = self._events.list_events(
            tenant_id=request.tenant_id,
            run_id=request.run_id,
        )
        event = CanonicalWorkflowEvent.build(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            step_id=request.step_id,
            event_type="workflow.step.delegated",
            correlation_id=request.correlation_id,
            causation_id=request.operation_id,
            dedupe_key=f"hub-task:{request.operation_id}",
            actor="hub",
            payload={
                "operation_id": request.operation_id,
                "task_kind": request.task_kind,
            },
        )
        stored = self._events.append(event, expected_sequence=len(current))
        ledger_event = side_effect_event(
            ledger_record,
            correlation_id=request.correlation_id,
            causation_id=stored.event_id,
        )
        stored_ledger = self._events.append(
            ledger_event,
            expected_sequence=stored.sequence,
        )
        return (stored.event_id, stored_ledger.event_id)

    def _record_retry_event(
        self,
        request: StepActivityInput,
        *,
        retry_id: str,
        category: str,
        used: int,
        maximum: int,
    ) -> None:
        current = self._events.list_events(tenant_id=request.tenant_id, run_id=request.run_id)
        dedupe_key = f"retry-budget:{retry_id}"
        expected_payload = {
            "retry_id": retry_id,
            "category": category,
            "used": used,
            "maximum": maximum,
            "remaining": max(0, maximum - used),
        }
        for existing in current:
            if existing.dedupe_key != dedupe_key:
                continue
            if (
                existing.workflow_id != request.workflow_id
                or existing.step_id != request.step_id
                or dict(existing.payload) != expected_payload
            ):
                raise WorkflowHubTaskError("workflow_retry_event_dedupe_conflict")
            return
        event = CanonicalWorkflowEvent.build(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            step_id=request.step_id,
            attempt=used + 1,
            event_type="workflow.budget.retry_consumed",
            correlation_id=request.correlation_id,
            causation_id=request.operation_id,
            dedupe_key=dedupe_key,
            actor="hub",
            payload=expected_payload,
        )
        self._events.append(event, expected_sequence=len(current))

    def _receipt(self, task: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        context = self._runtime_context(task)
        tenant_id = self._dispatch_tenant(task)
        ledger = self._ledger.get(tenant_id=tenant_id, operation_id=operation_id)
        ledger_state = str(getattr(ledger, "status", "") or "uncertain")
        task_status = self._receipt_status(str(task.get("status") or "created"))
        reason_code = str(task.get("status_reason_code") or "")
        if ledger_state == "uncertain":
            task_status = "uncertain"
            reason_code = reason_code or "workflow_task_outcome_uncertain"
        elif task_status == "completed" and ledger_state != "completed":
            task_status = "uncertain"
            reason_code = "workflow_task_ledger_completion_mismatch"
        verification = dict(task.get("verification_status") or {}).get("workflow_runtime") or {}
        return {
            "schema": HUB_TASK_RECEIPT_SCHEMA,
            "hub_task_id": str(task.get("id") or ""),
            "operation_id": operation_id,
            "status": task_status,
            "authorization_state": str(context.get("authorization_state") or "invalid"),
            "ledger_state": ledger_state,
            "artifact_refs": list(verification.get("artifact_refs") or []),
            "canonical_event_refs": list(context.get("canonical_event_refs") or []),
            "checkpoint_ref": str(verification.get("checkpoint_ref") or ""),
            "reason_code": reason_code,
        }

    def _dispatch_tenant(self, task: dict[str, Any]) -> str:
        raw = self._codec.open(dict(self._runtime_context(task).get("dispatch") or {}))
        return str(raw.get("tenant_id") or "")

    @staticmethod
    def _runtime_context(task: dict[str, Any]) -> dict[str, Any]:
        worker_context = task.get("worker_execution_context") or {}
        context = worker_context.get("workflow_runtime") if isinstance(worker_context, dict) else None
        if not isinstance(context, dict):
            raise WorkflowHubTaskError("workflow_hub_task_context_missing")
        return context

    @staticmethod
    def _task_id(operation_id: str) -> str:
        encoded = base64.b32encode(operation_id.encode("utf-8")).decode("ascii").rstrip("=").lower()
        return f"wft-{encoded[:52]}"

    @staticmethod
    def _receipt_status(status: str) -> str:
        normalized = status.strip().lower()
        return {
            "todo": "created",
            "created": "created",
            "delegated": "delegated",
            "assigned": "assigned",
            "in_progress": "running",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }.get(normalized, "uncertain")
