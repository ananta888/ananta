"""Hub-owned queue boundary for delegated workflow-adapter tasks.

This module is intentionally free of worker imports.  It issues one signed,
fenced execution contract and persists it through :class:`TaskQueueService`;
workers can only consume the resulting task through the normal Hub queue.

The submission/receipt contracts live in
:mod:`agent.services.workflow_adapter_task_queue_contracts` and are
re-exported here for existing importers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Mapping, Protocol

from agent.services.workflow_adapter_event_projection import (
    WorkflowAdapterCanonicalEventProjector,
    WorkflowAdapterResultProjectionError,
)
from agent.services.workflow_adapter_task_contract_factory import (
    build_workflow_adapter_task_contract,
)
from agent.services.workflow_adapter_task_identity import (
    hub_task_id as _hub_task_id,
)
from agent.services.workflow_adapter_task_identity import (
    hub_task_id_from_context as _hub_task_id_from_context,
)
from agent.services.workflow_adapter_task_identity import (
    operation_id as _operation_id,
)
from agent.services.workflow_adapter_task_identity import (
    submission_request_digest as _submission_request_digest,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WORKFLOW_ADAPTER_CONTROL_SCHEMA as WORKFLOW_ADAPTER_CONTROL_SCHEMA,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WORKFLOW_ADAPTER_RECEIPT_SCHEMA as WORKFLOW_ADAPTER_RECEIPT_SCHEMA,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WORKFLOW_ADAPTER_STATUS_SCHEMA as WORKFLOW_ADAPTER_STATUS_SCHEMA,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WorkflowAdapterQueueError as WorkflowAdapterQueueError,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WorkflowAdapterTaskQueuePort as WorkflowAdapterTaskQueuePort,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WorkflowAdapterTaskReceipt as WorkflowAdapterTaskReceipt,
)
from agent.services.workflow_adapter_task_queue_contracts import (
    WorkflowAdapterTaskSubmission as WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_runtime import (
    EventStore,
    ExecutionOwnershipStore,
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
    ownership_event,
)
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderExecutionBinding,
)
from ananta_contracts.workflow_adapter_task import (
    WORKFLOW_ADAPTER_RUNTIME_PATH,
    WORKFLOW_ADAPTER_TASK_SCHEMA,
    WORKFLOW_ADAPTER_TASK_VERIFICATION_SCHEMA,
    WorkflowAdapterTaskContractError,
    WorkflowAdapterTaskResult,
)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
logger = logging.getLogger(__name__)


class TaskQueueMutationPort(Protocol):
    def ingest_task(self, **values: Any) -> None: ...


class TaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class TaskRuntimeMutationPort(Protocol):
    def update_local_task_status(
        self, task_id: str, status: str, **values: Any
    ) -> None: ...


class WorkflowAdapterTaskQueueService:
    """Create and control one real Hub task; never execute adapter code."""

    def __init__(
        self,
        *,
        task_queue: TaskQueueMutationPort,
        task_repository: TaskRepositoryPort,
        task_runtime: TaskRuntimeMutationPort,
        ownership: ExecutionOwnershipStore,
        authorization_keys: HmacKeyRing,
        authorization_grants: WorkflowAuthorizationGrantPort | None = None,
        events: EventStore | None = None,
        owner_id: str = "workflow-adapter-task-queue",
        lease_seconds: float = 1800.0,
        clock=time.time,
    ) -> None:
        self._queue = task_queue
        self._repository = task_repository
        self._runtime = task_runtime
        self._ownership = ownership
        self._authorization_keys = authorization_keys
        self._authorization_grants = (
            authorization_grants
            or InMemoryWorkflowAuthorizationGrantService(clock=clock)
        )
        self._events = events
        self._result_projector = (
            WorkflowAdapterCanonicalEventProjector(events)
            if events is not None
            else None
        )
        self._owner_id = str(owner_id)
        self._lease_seconds = max(60.0, min(float(lease_seconds), 86_400.0))
        self._clock = clock

    def submit(
        self, submission: WorkflowAdapterTaskSubmission
    ) -> WorkflowAdapterTaskReceipt:
        submission.validate()
        hub_task_id = _hub_task_id(submission)
        operation_id = _operation_id(submission)
        payload_digest = _submission_request_digest(submission)
        existing = self._repository.get_by_id(hub_task_id)
        if existing is not None:
            context = _task_context(existing)
            self._assert_context_owner(
                context,
                tenant_id=submission.tenant_id,
                subject_id=submission.subject_id,
                hub_task_id=hub_task_id,
            )
            if (
                str(context.get("operation_id") or "") != operation_id
                or str(context.get("request_digest") or "") != payload_digest
            ):
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_idempotency_conflict", status_code=409
                )
            return self._receipt(existing, duplicate=True)

        claim = self._ownership.claim(
            tenant_id=submission.tenant_id,
            workflow_id=submission.workflow_id,
            run_id=submission.run_id,
            step_id=submission.step_id,
            owner_id=self._owner_id,
            lease_seconds=min(
                self._lease_seconds, submission.authorization_ttl_seconds
            ),
            maximum_retries=submission.maximum_retries,
            now=float(self._clock()),
        )
        if not claim.acquired:
            raise WorkflowAdapterQueueError(
                f"workflow_adapter_ownership_denied:{claim.reason}", status_code=409
            )
        ownership = claim.ownership
        authorization: RuntimeAuthorizationEnvelope | None = None
        try:
            authorization = RuntimeAuthorizationEnvelope.issue(
                key_ring=self._authorization_keys,
                tenant_id=submission.tenant_id,
                workflow_id=submission.workflow_id,
                run_id=submission.run_id,
                step_id=submission.step_id,
                plan_hash=submission.plan_hash,
                policy_version=submission.policy_version,
                allowed_tools=submission.allowed_tools,
                allowed_artifacts=submission.allowed_artifacts,
                allowed_provider_bindings=tuple(
                    ProviderBindingAuthorization.from_binding(item.binding)
                    for item in submission.provider_profile_bindings
                )
                or (
                    (
                        ProviderBindingAuthorization.from_binding(
                            submission.provider_binding
                        ),
                    )
                    if submission.provider_binding is not None
                    else ()
                ),
                provider_attempt_plan=submission.provider_attempt_plan,
                budgets={
                    "retries": submission.maximum_retries,
                    "attempts": submission.maximum_retries + 1,
                    **(
                        {
                            "provider_attempts": (
                                submission.provider_maximum_attempts
                            )
                        }
                        if submission.provider_maximum_attempts
                        else {}
                    ),
                    "tokens": submission.max_total_tokens,
                    "cost_micros": submission.max_cost_micros,
                },
                ttl_seconds=submission.authorization_ttl_seconds,
                now=float(self._clock()),
            )
            self._authorization_grants.grant(authorization)
            contract = build_workflow_adapter_task_contract(
                submission,
                authorization=authorization,
                attempt_id=ownership.attempt_id,
                fencing_token=ownership.fencing_token,
            )
        except Exception as exc:
            if authorization is not None:
                self._revoke_grant_safely(
                    authorization.envelope_id,
                    reason_code="workflow_adapter_contract_creation_failed",
                )
            self._fail_claim_safely(
                ownership,
                failure_code="workflow_adapter_contract_creation_failed",
            )
            raise WorkflowAdapterQueueError(
                "workflow_adapter_contract_creation_failed", status_code=503
            ) from exc
        context = {
            **contract.__dict__,
            "authorization_envelope": contract.authorization_envelope.to_dict(),
            "provider_binding": (
                contract.provider_binding.to_dict()
                if contract.provider_binding is not None
                else None
            ),
            "provider_profile_bindings": [
                item.to_dict() for item in contract.provider_profile_bindings
            ],
            "provider_attempt_plan": [
                item.to_dict() for item in contract.provider_attempt_plan
            ],
            "subject_id": submission.subject_id,
            "operation_id": operation_id,
            "idempotency_key": submission.idempotency_key,
            "request_digest": payload_digest,
            "ownership_revision": ownership.revision,
            "owner_id": ownership.owner_id,
        }
        try:
            self._queue.ingest_task(
                task_id=hub_task_id,
                status="created",
                title=(
                    f"{submission.adapter_kind} {submission.command}: "
                    f"{submission.task_type}"
                )[:200],
                description=(
                    "Execute one Hub-authorized workflow adapter operation. "
                    f"workflow={submission.workflow_id} run={submission.run_id}"
                ),
                priority="medium",
                created_by=f"workflow-adapter-control:{submission.subject_id[:80]}",
                source="workflow_runtime",
                tags=["workflow-runtime", "hub-delegated", submission.adapter_kind],
                event_type="workflow_adapter_task_created",
                event_channel="hub_task_queue",
                event_details={
                    "workflow_id": submission.workflow_id,
                    "run_id": submission.run_id,
                    "step_id": submission.step_id,
                    "operation_id": operation_id,
                    "adapter_kind": submission.adapter_kind,
                    "command": submission.command,
                    "provider_binding_id": (
                        submission.provider_binding.binding_id
                        if submission.provider_binding is not None
                        else ""
                    ),
                },
                extra_fields={
                    "plan_id": submission.plan_hash,
                    "plan_node_id": submission.step_id,
                    "task_kind": submission.task_type,
                    "required_capabilities": [
                        f"workflow.adapter.{submission.adapter_kind}",
                    ],
                    "derivation_reason": "workflow_adapter_hub_delegation",
                    "worker_execution_context": context,
                    "verification_spec": {
                        "schema": WORKFLOW_ADAPTER_TASK_VERIFICATION_SCHEMA,
                        "artifact_first": True,
                    },
                },
            )
        except Exception as exc:
            self._revoke_grant_safely(
                authorization.envelope_id,
                reason_code="workflow_adapter_queue_persistence_failed",
            )
            self._fail_claim_safely(
                ownership,
                failure_code="workflow_adapter_queue_persistence_failed",
            )
            raise WorkflowAdapterQueueError(
                "workflow_adapter_queue_persistence_failed", status_code=503
            ) from exc
        created = self._repository.get_by_id(hub_task_id)
        if created is None:
            self._revoke_grant_safely(
                authorization.envelope_id,
                reason_code="workflow_adapter_queue_persistence_failed",
            )
            self._fail_claim_safely(
                ownership,
                failure_code="workflow_adapter_queue_persistence_failed",
            )
            raise WorkflowAdapterQueueError(
                "workflow_adapter_queue_persistence_failed", status_code=503
            )
        try:
            self._record_ownership(
                ownership,
                correlation_id=submission.correlation_id or submission.run_id,
            )
        except Exception:
            logger.warning(
                "workflow adapter ownership projection failed for run=%s step=%s",
                submission.run_id,
                submission.step_id,
            )
        return self._receipt(created, duplicate=False)

    def _fail_claim_safely(self, ownership: Any, *, failure_code: str) -> None:
        try:
            self._ownership.fail_attempt(
                tenant_id=ownership.tenant_id,
                run_id=ownership.run_id,
                step_id=ownership.step_id,
                attempt_id=ownership.attempt_id,
                owner_id=ownership.owner_id,
                fencing_token=ownership.fencing_token,
                expected_revision=ownership.revision,
                failure_code=failure_code,
                now=float(self._clock()),
            )
        except Exception:
            logger.warning(
                "workflow adapter ownership cleanup failed for run=%s step=%s",
                ownership.run_id,
                ownership.step_id,
            )

    def _record_ownership(self, ownership: Any, *, correlation_id: str) -> None:
        if self._events is None:
            return
        event = ownership_event(
            ownership,
            correlation_id=correlation_id or ownership.run_id,
            causation_id=f"workflow-adapter:{ownership.attempt_id}",
        )
        current = self._events.list_events(
            tenant_id=ownership.tenant_id,
            run_id=ownership.run_id,
        )
        if any(item.dedupe_key == event.dedupe_key for item in current):
            return
        self._events.append(event, expected_sequence=len(current))

    def _revoke_grant_safely(self, envelope_id: str, *, reason_code: str) -> None:
        try:
            self._authorization_grants.revoke(
                envelope_id,
                reason_code=reason_code,
            )
        except Exception:
            logger.warning(
                "workflow adapter authorization grant cleanup failed envelope=%s",
                envelope_id,
            )

    def status(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> dict[str, Any]:
        return self._status(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
            acknowledge=True,
        )

    def inspect(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> dict[str, Any]:
        """Read a task projection without acknowledging results or appending events.

        Hub reconcilers deliberately use :meth:`status` to consume a terminal
        result. Query routes use this method so GET/status cannot become a
        hidden scheduling or checkpoint mutation.
        """

        return self._status(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
            acknowledge=False,
        )

    def _status(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        hub_task_id: str,
        acknowledge: bool,
    ) -> dict[str, Any]:
        task, context = self._owned_task(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
        )
        verification = dict(_task_value(task, "verification_status") or {})
        raw_result = verification.get("workflow_adapter_task_result")
        result: dict[str, Any] | None = None
        if isinstance(raw_result, Mapping):
            try:
                result = WorkflowAdapterTaskResult.from_mapping(raw_result).to_dict()
            except WorkflowAdapterTaskContractError as exc:
                raise WorkflowAdapterQueueError(exc.reason_code, status_code=409) from exc
            if result["hub_task_id"] != hub_task_id:
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_result_task_binding_mismatch", status_code=409
                )
        status = str(_task_value(task, "status") or "created").strip().lower()
        if status in _TERMINAL_STATUSES and result is None:
            result = {
                "schema": "ananta.workflow-adapter-worker-result.v1",
                "hub_task_id": hub_task_id,
                "adapter_kind": str(context.get("adapter_kind") or "langgraph"),
                "status": "cancelled" if status == "cancelled" else "failed",
                "reason_code": "workflow_adapter_result_contract_missing",
                "summary": "",
                "artifacts": [],
                "sources": [],
                "adapter_result": None,
            }
        if acknowledge and status in {"completed", "failed"} and result is not None:
            if self._result_projector is not None:
                try:
                    self._result_projector.project(
                        context=context,
                        result=result,
                        hub_task_id=hub_task_id,
                    )
                except WorkflowAdapterResultProjectionError as exc:
                    raise WorkflowAdapterQueueError(str(exc), status_code=409) from exc
            self._acknowledge_terminal_result(
                context=context,
                result=result,
                task_status=status,
            )
        return {
            "schema": WORKFLOW_ADAPTER_STATUS_SCHEMA,
            "hub_task_id": hub_task_id,
            "operation_id": str(context.get("operation_id") or ""),
            "tenant_id": tenant_id,
            "workflow_id": str(context.get("workflow_id") or ""),
            "run_id": str(context.get("run_id") or ""),
            "step_id": str(context.get("step_id") or ""),
            "adapter_kind": str(context.get("adapter_kind") or ""),
            "command": str(context.get("command") or ""),
            "status": status,
            "terminal": status in _TERMINAL_STATUSES,
            "reason_code": str(_task_value(task, "status_reason_code") or ""),
            "provider_binding": (
                dict(context["provider_binding"])
                if isinstance(context.get("provider_binding"), Mapping)
                else None
            ),
            "result": result,
        }

    def cancel(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        hub_task_id: str,
        reason: str,
    ) -> dict[str, Any]:
        task, context = self._owned_task(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
        )
        current = str(_task_value(task, "status") or "created").strip().lower()
        if current not in _TERMINAL_STATUSES:
            bounded_reason = str(reason or "workflow_adapter_cancelled").strip()[:240]
            self._terminate_ownership(
                context=context,
                failure_code="workflow_adapter_cancelled",
            )
            authorization = context.get("authorization_envelope")
            if isinstance(authorization, Mapping):
                envelope_id = str(authorization.get("envelope_id") or "")
                if envelope_id:
                    self._revoke_grant_safely(
                        envelope_id,
                        reason_code="workflow_adapter_cancelled",
                    )
            self._runtime.update_local_task_status(
                hub_task_id,
                "cancelled",
                event_type="workflow_adapter_task_cancelled",
                event_actor=f"workflow-adapter-control:{subject_id[:80]}",
                event_details={
                    "workflow_id": str(context.get("workflow_id") or ""),
                    "run_id": str(context.get("run_id") or ""),
                    "reason": bounded_reason,
                },
                status_reason_code="workflow_adapter_cancelled",
            )
        return self.status(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
        )

    def history(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> tuple[dict[str, Any], ...]:
        task, context = self._owned_task(
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=hub_task_id,
        )
        workflow_id = str(context.get("workflow_id") or "")
        run_id = str(context.get("run_id") or "")
        step_id = str(context.get("step_id") or "")
        if self._events is not None:
            canonical = self._events.list_events(
                tenant_id=tenant_id,
                run_id=run_id,
            )
            if canonical:
                return tuple(event.to_dict() for event in canonical)
        events: list[dict[str, Any]] = []
        for index, item in enumerate(list(_task_value(task, "history") or []), start=1):
            if not isinstance(item, Mapping):
                continue
            details = item.get("details")
            events.append(
                {
                    "event_id": str(item.get("event_id") or f"{hub_task_id}:{index}"),
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "step_id": step_id,
                    "event_type": _history_event_type(str(item.get("event_type") or "")),
                    "occurred_at": float(
                        item.get("timestamp") or item.get("occurred_at") or self._clock()
                    ),
                    "payload": {
                        **(dict(details) if isinstance(details, Mapping) else {}),
                        "hub_task_id": hub_task_id,
                        "task_status": str(_task_value(task, "status") or ""),
                    },
                }
            )
        return tuple(events)

    def _acknowledge_terminal_result(
        self,
        *,
        context: Mapping[str, Any],
        result: Mapping[str, Any],
        task_status: str,
    ) -> None:
        current = self._ownership.get(
            tenant_id=str(context.get("tenant_id") or ""),
            run_id=str(context.get("run_id") or ""),
            step_id=str(context.get("step_id") or ""),
        )
        if current is None or current.status != "active":
            return
        if (
            current.attempt_id != str(context.get("attempt_id") or "")
            or current.fencing_token != int(context.get("fencing_token") or 0)
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_result_fencing_mismatch", status_code=409
            )
        result_ack_key = hashlib.sha256(
            json.dumps(
                dict(result),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        try:
            updated = self._ownership.acknowledge_result(
                tenant_id=current.tenant_id,
                run_id=current.run_id,
                step_id=current.step_id,
                attempt_id=current.attempt_id,
                owner_id=current.owner_id,
                fencing_token=current.fencing_token,
                expected_revision=current.revision,
                result_ack_key=f"{task_status}:{result_ack_key}",
                now=float(self._clock()),
            )
        except Exception as exc:
            latest = self._ownership.get(
                tenant_id=current.tenant_id,
                run_id=current.run_id,
                step_id=current.step_id,
            )
            if latest is not None and latest.status == "completed":
                return
            raise WorkflowAdapterQueueError(
                "workflow_adapter_result_acknowledgement_failed", status_code=409
            ) from exc
        self._record_ownership(updated, correlation_id=current.run_id)

    def _terminate_ownership(
        self, *, context: Mapping[str, Any], failure_code: str
    ) -> None:
        current = self._ownership.get(
            tenant_id=str(context.get("tenant_id") or ""),
            run_id=str(context.get("run_id") or ""),
            step_id=str(context.get("step_id") or ""),
        )
        if current is None or current.status != "active":
            return
        if (
            current.attempt_id != str(context.get("attempt_id") or "")
            or current.fencing_token != int(context.get("fencing_token") or 0)
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_cancel_fencing_mismatch", status_code=409
            )
        try:
            updated = self._ownership.fail_attempt(
                tenant_id=current.tenant_id,
                run_id=current.run_id,
                step_id=current.step_id,
                attempt_id=current.attempt_id,
                owner_id=current.owner_id,
                fencing_token=current.fencing_token,
                expected_revision=current.revision,
                failure_code=str(failure_code),
                now=float(self._clock()),
            )
        except Exception as exc:
            latest = self._ownership.get(
                tenant_id=current.tenant_id,
                run_id=current.run_id,
                step_id=current.step_id,
            )
            if latest is not None and latest.status != "active":
                return
            raise WorkflowAdapterQueueError(
                "workflow_adapter_cancel_ownership_failed", status_code=409
            ) from exc
        self._record_ownership(updated, correlation_id=current.run_id)

    def _owned_task(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> tuple[Any, dict[str, Any]]:
        normalized = str(hub_task_id or "").strip()
        if not normalized or len(normalized) > 256:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_id_invalid", status_code=400
            )
        task = self._repository.get_by_id(normalized)
        if task is None:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )
        context = _task_context(task)
        self._assert_context_owner(
            context,
            tenant_id=tenant_id,
            subject_id=subject_id,
            hub_task_id=normalized,
        )
        return task, context

    @staticmethod
    def _assert_context_owner(
        context: Mapping[str, Any], *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> None:
        if (
            str(context.get("schema") or "") != WORKFLOW_ADAPTER_TASK_SCHEMA
            or str(context.get("runtime_path") or "") != WORKFLOW_ADAPTER_RUNTIME_PATH
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )
        if (
            str(context.get("tenant_id") or "") != str(tenant_id)
            or str(context.get("subject_id") or "") != str(subject_id)
        ):
            # Cross-tenant and cross-principal identifiers are deliberately
            # indistinguishable from missing tasks.
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )
        if _hub_task_id_from_context(context) != hub_task_id:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_binding_mismatch", status_code=409
            )

    @staticmethod
    def _receipt(task: Any, *, duplicate: bool) -> WorkflowAdapterTaskReceipt:
        context = _task_context(task)
        return WorkflowAdapterTaskReceipt(
            hub_task_id=str(_task_value(task, "id") or ""),
            workflow_id=str(context.get("workflow_id") or ""),
            run_id=str(context.get("run_id") or ""),
            step_id=str(context.get("step_id") or ""),
            operation_id=str(context.get("operation_id") or ""),
            adapter_kind=str(context.get("adapter_kind") or ""),
            command=str(context.get("command") or ""),
            accepted=True,
            duplicate=duplicate,
            status=str(_task_value(task, "status") or "created"),
            provider_binding=(
                ProviderExecutionBinding.from_mapping(context["provider_binding"])
                if isinstance(context.get("provider_binding"), Mapping)
                else None
            ),
        )


def _task_context(task: Any) -> dict[str, Any]:
    return dict(_task_value(task, "worker_execution_context") or {})


def _task_value(task: Any, name: str) -> Any:
    if isinstance(task, Mapping):
        return task.get(name)
    return getattr(task, name, None)


def _history_event_type(raw: str) -> str:
    value = str(raw or "")
    if value.endswith("cancelled"):
        return "workflow.run.cancelled"
    if value.endswith("consumed") or value.endswith("completed"):
        return "workflow.run.completed"
    if value.endswith("failed"):
        return "workflow.run.failed"
    if value.endswith("created") or value.endswith("delegated"):
        return "workflow.run.started"
    if value.endswith("claimed") or value.endswith("assigned"):
        return "workflow.node.started"
    return "workflow.status.updated"


def build_workflow_adapter_task_queue_service() -> WorkflowAdapterTaskQueueService:
    """Compatibility facade for the split production composition root."""

    from agent.services.workflow_adapter_task_queue_composition import (
        build_workflow_adapter_task_queue_service as build,
    )

    return build()


__all__ = [
    "WORKFLOW_ADAPTER_CONTROL_SCHEMA",
    "WORKFLOW_ADAPTER_RECEIPT_SCHEMA",
    "WORKFLOW_ADAPTER_STATUS_SCHEMA",
    "WorkflowAdapterQueueError",
    "WorkflowAdapterTaskQueuePort",
    "WorkflowAdapterTaskQueueService",
    "WorkflowAdapterTaskReceipt",
    "WorkflowAdapterTaskSubmission",
    "build_workflow_adapter_task_queue_service",
]
