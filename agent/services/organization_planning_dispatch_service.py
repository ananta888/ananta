"""Persistent outbox pump for Organization planning task dispatch.

``PlanningTaskMaterializationService.claim_next`` creates the intent and Task
CAS in one transaction.  This service is the only component that crosses the
Hub-to-Worker port.  It records an acceptance receipt in a later transaction
and can reconstruct that receipt from the authoritative WorkerJob after a
process interruption.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, select

from agent.db_models import PlanningTaskDispatchDB, PlanningTaskMappingDB, TaskDB, WorkerJobDB
from agent.models import TaskDelegationRequest
from agent.ports.planning_dispatch import (
    PlanningDispatchAcceptance,
    PlanningDispatchEnvelope,
    PlanningWorkerDelegationPort,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)

_SAFE_REASON = re.compile(r"^[a-z0-9_.:-]{1,191}$")
_ACTIVE_TASK_STATES = frozenset({"assigned", "in_progress"})


class PlanningDispatchError(PlanningTransitionError):
    """Stable error raised by the outbox control plane."""


class HubTaskDelegationAdapter:
    """Adapter over the existing Hub TaskOrchestration delegation service."""

    def dispatch(
        self,
        envelope: PlanningDispatchEnvelope,
    ) -> PlanningDispatchAcceptance:
        from agent.services.service_registry import get_core_services

        services = get_core_services()
        task = dict(envelope.task)
        context = dict(task.get("worker_execution_context") or {})
        request = TaskDelegationRequest(
            agent_url=envelope.requested_worker_id,
            subtask_description=str(task.get("description") or task.get("title") or envelope.plan_task_id),
            priority=str(task.get("priority") or "Medium"),
            task_kind=str(task.get("task_kind") or "implementation"),
            retrieval_intent=(str(task.get("retrieval_intent") or "").strip() or None),
            required_context_scope=(str(task.get("required_context_scope") or "").strip() or None),
            preferred_bundle_mode=(str(task.get("preferred_bundle_mode") or "").strip() or None),
            required_capabilities=[str(value) for value in list(task.get("required_capabilities") or []) if str(value)],
            context_query=str(task.get("description") or task.get("title") or ""),
            allowed_tools=[str(value) for value in list(context.get("allowed_tools") or []) if str(value)],
            expected_output_schema=(
                dict(context.get("expected_output_schema") or {})
                if isinstance(context.get("expected_output_schema"), dict)
                else {}
            ),
        )
        result = services.task_orchestration_service.delegate_task(
            task_id=envelope.internal_task_id,
            data=request,
            worker_job_service=services.worker_job_service,
            worker_contract_service=services.worker_contract_service,
            agent_registry_service=services.agent_registry_service,
            result_memory_service=services.result_memory_service,
            verification_service=services.verification_service,
        )
        if result.get("error"):
            reason = str(result.get("error") or "planning_worker_dispatch_failed")
            raise PlanningDispatchError(reason if _SAFE_REASON.fullmatch(reason) else "planning_worker_dispatch_failed")
        data = dict(result.get("data") or {})
        acceptance = PlanningDispatchAcceptance(
            worker_job_id=str(data.get("worker_job_id") or ""),
            assignment_id=str(data.get("subtask_id") or ""),
            worker_id=str(data.get("agent_url") or ""),
            receipt={
                "status": str(data.get("status") or "delegated"),
                "selected_by_policy": bool(data.get("selected_by_policy")),
                "policy_decision_id": str(data.get("policy_decision_id") or "") or None,
                "context_bundle_id": str(data.get("context_bundle_id") or "") or None,
            },
        )
        self._validate_acceptance(acceptance)
        return acceptance

    @staticmethod
    def _validate_acceptance(acceptance: PlanningDispatchAcceptance) -> None:
        if any(
            not str(value or "").strip()
            for value in (
                acceptance.worker_job_id,
                acceptance.assignment_id,
                acceptance.worker_id,
            )
        ):
            raise PlanningDispatchError("planning_worker_dispatch_receipt_invalid")


class PlanningDispatchOutboxService:
    """Claim, deliver, accept and replay durable planning dispatch intents."""

    def __init__(
        self,
        *,
        delegation_port: PlanningWorkerDelegationPort | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
        clock: Callable[[], float] = time.time,
        processing_lease_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        self._delegation = delegation_port or HubTaskDelegationAdapter()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._clock = clock
        self._lease_seconds = max(15, min(int(processing_lease_seconds), 300))
        self._max_attempts = max(1, min(int(max_attempts), 20))

    def pump_intent(
        self,
        *,
        context: PlanningOperationContext,
        dispatch_intent_id: str,
        pump_owner: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        owner = str(pump_owner or "").strip()
        if not owner or len(owner) > 191:
            raise PlanningDispatchError("planning_dispatch_pump_owner_required")
        with planning_scope_lock(f"planning-dispatch-pump:{dispatch_intent_id}"):
            claimed = self._claim(
                context=context,
                dispatch_intent_id=dispatch_intent_id,
                pump_owner=owner,
            )
        if isinstance(claimed, dict):
            return claimed
        envelope = claimed
        try:
            acceptance = self._delegation.dispatch(envelope)
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "") or str(exc) or "planning_worker_dispatch_failed")
            safe_reason = reason if _SAFE_REASON.fullmatch(reason) else "planning_worker_dispatch_failed"
            return self._record_failure(
                context=context,
                envelope=envelope,
                pump_owner=owner,
                reason_code=safe_reason,
            )
        return self._accept(
            context=context,
            envelope=envelope,
            pump_owner=owner,
            acceptance=acceptance,
        )

    def retry(
        self,
        *,
        context: PlanningOperationContext,
        dispatch_intent_id: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        now = self._clock()
        with planning_scope_lock(f"planning-dispatch-pump:{dispatch_intent_id}"), self._uow_factory() as uow:
            assert uow.session is not None
            row = self._scoped_dispatch(
                uow.session,
                context=context,
                dispatch_intent_id=dispatch_intent_id,
                for_update=True,
            )
            if row.status == "dispatched":
                return self._response(row, replayed=True)
            if row.status == "dispatching" and float(row.processing_lease_expires_at or 0) > now:
                raise PlanningDispatchError("planning_dispatch_in_progress")
            if row.attempt >= self._max_attempts:
                raise PlanningDispatchError("planning_dispatch_retry_exhausted")
            row.status = "retry_pending"
            row.next_attempt_at = now
            row.processing_owner = None
            row.processing_started_at = None
            row.processing_lease_expires_at = None
            row.updated_at = now
            uow.session.add(row)
        return self._response(row, replayed=False)

    def pump_due(
        self,
        *,
        context: PlanningOperationContext,
        pump_owner: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        self._authorize(context)
        bounded = max(1, min(int(limit), 50))
        now = self._clock()
        with self._uow_factory() as uow:
            assert uow.session is not None
            ids = list(
                uow.session.exec(
                    select(PlanningTaskDispatchDB.dispatch_intent_id)
                    .where(
                        PlanningTaskDispatchDB.tenant_id == context.tenant_id,
                        PlanningTaskDispatchDB.project_id == context.project_id,
                        PlanningTaskDispatchDB.organization_id == context.organization_id,
                        or_(
                            (
                                PlanningTaskDispatchDB.status.in_(("pending_dispatch", "retry_pending"))
                                & (PlanningTaskDispatchDB.next_attempt_at <= now)
                            ),
                            (
                                (PlanningTaskDispatchDB.status == "dispatching")
                                & (PlanningTaskDispatchDB.processing_lease_expires_at <= now)
                            ),
                        ),
                    )
                    .order_by(
                        PlanningTaskDispatchDB.next_attempt_at.asc(),  # type: ignore[attr-defined]
                        PlanningTaskDispatchDB.created_at.asc(),  # type: ignore[attr-defined]
                    )
                    .limit(bounded)
                ).all()
            )
        receipts: list[dict[str, Any]] = []
        for dispatch_id in ids:
            try:
                receipts.append(
                    self.pump_intent(
                        context=context,
                        dispatch_intent_id=str(dispatch_id),
                        pump_owner=pump_owner,
                    )
                )
            except PlanningDispatchError as exc:
                receipts.append(
                    {
                        "dispatch_intent_id": str(dispatch_id),
                        "status": "deferred",
                        "reason_code": exc.reason_code,
                    }
                )
        return receipts

    def _claim(
        self,
        *,
        context: PlanningOperationContext,
        dispatch_intent_id: str,
        pump_owner: str,
    ) -> PlanningDispatchEnvelope | dict[str, Any]:
        now = self._clock()
        with self._uow_factory() as uow:
            assert uow.session is not None
            row = self._scoped_dispatch(
                uow.session,
                context=context,
                dispatch_intent_id=dispatch_intent_id,
                for_update=True,
            )
            task = self._scoped_task(uow.session, context=context, dispatch=row)
            recovered = self._recover_acceptance(uow.session, row=row, task=task, now=now)
            if recovered:
                return self._response(row, replayed=True)
            if row.status == "dispatched":
                return self._response(row, replayed=True)
            if row.status == "dispatch_failed":
                raise PlanningDispatchError("planning_dispatch_retry_required")
            if row.status == "dispatching" and float(row.processing_lease_expires_at or 0) > now:
                raise PlanningDispatchError("planning_dispatch_in_progress")
            if row.status not in {"pending_dispatch", "retry_pending", "dispatching"}:
                raise PlanningDispatchError("planning_dispatch_state_invalid")
            if float(row.next_attempt_at or 0) > now:
                raise PlanningDispatchError("planning_dispatch_backoff_active")
            if row.status in {"retry_pending", "dispatching"}:
                if row.attempt >= self._max_attempts:
                    row.status = "dispatch_failed"
                    row.updated_at = now
                    uow.session.add(row)
                    raise PlanningDispatchError("planning_dispatch_retry_exhausted")
                row.attempt += 1
            row.status = "dispatching"
            row.processing_owner = pump_owner
            row.processing_started_at = now
            row.processing_lease_expires_at = now + self._lease_seconds
            row.updated_at = now
            row.last_error_code = None
            uow.session.add(row)
            mapping = uow.session.get(PlanningTaskMappingDB, row.task_mapping_id)
            if mapping is None or mapping.internal_task_id != row.internal_task_id:
                raise PlanningDispatchError("planning_dispatch_mapping_invalid")
            task_payload = task.model_dump(mode="json")
            return PlanningDispatchEnvelope(
                dispatch_intent_id=row.dispatch_intent_id,
                idempotency_key=row.idempotency_key,
                lease_id=row.lease_id,
                attempt=row.attempt,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                organization_id=row.organization_id,
                goal_id=row.goal_id,
                track_revision_id=row.track_revision_id,
                plan_task_id=mapping.plan_task_id,
                internal_task_id=row.internal_task_id,
                requested_worker_id=row.requested_worker_id,
                task=task_payload,
            )

    def _accept(
        self,
        *,
        context: PlanningOperationContext,
        envelope: PlanningDispatchEnvelope,
        pump_owner: str,
        acceptance: PlanningDispatchAcceptance,
    ) -> dict[str, Any]:
        HubTaskDelegationAdapter._validate_acceptance(acceptance)
        now = self._clock()
        with self._uow_factory() as uow:
            assert uow.session is not None
            row = self._scoped_dispatch(
                uow.session,
                context=context,
                dispatch_intent_id=envelope.dispatch_intent_id,
                for_update=True,
            )
            if row.status == "dispatched":
                return self._response(row, replayed=True)
            if (
                row.status != "dispatching"
                or row.processing_owner != pump_owner
                or row.attempt != envelope.attempt
                or row.lease_id != envelope.lease_id
            ):
                raise PlanningDispatchError("planning_dispatch_acceptance_stale")
            if row.requested_worker_id and row.requested_worker_id != acceptance.worker_id:
                raise PlanningDispatchError("planning_dispatch_worker_mismatch")
            task = self._scoped_task(uow.session, context=context, dispatch=row)
            if task.current_worker_job_id and task.current_worker_job_id != acceptance.worker_job_id:
                raise PlanningDispatchError("planning_dispatch_worker_job_conflict")
            row.status = "dispatched"
            row.worker_job_id = acceptance.worker_job_id
            row.assignment_id = acceptance.assignment_id
            row.accepted_worker_id = acceptance.worker_id
            row.transport_receipt = dict(acceptance.receipt or {})
            row.accepted_at = now
            row.updated_at = now
            row.processing_owner = None
            row.processing_started_at = None
            row.processing_lease_expires_at = None
            task.status = "in_progress"
            task.assigned_agent_url = acceptance.worker_id
            task.current_worker_job_id = acceptance.worker_job_id
            task.status_reason_code = "planning_dispatch_accepted"
            task.worker_execution_context = {
                **dict(task.worker_execution_context or {}),
                "planning_dispatch": {
                    "schema": "organization_planning_dispatch.v1",
                    "dispatch_intent_id": row.dispatch_intent_id,
                    "lease_id": row.lease_id,
                    "attempt": row.attempt,
                    "track_revision_id": row.track_revision_id,
                    "plan_task_id": envelope.plan_task_id,
                    "status": "dispatched",
                    "assignment_id": acceptance.assignment_id,
                    "worker_job_id": acceptance.worker_job_id,
                    "worker_id": acceptance.worker_id,
                },
            }
            task.history = [
                *list(task.history or []),
                {
                    "timestamp": now,
                    "status": "in_progress",
                    "event_type": "organization_planning_dispatch_accepted",
                    "actor": "hub:planning_dispatch_outbox",
                    "details": {
                        "dispatch_intent_id": row.dispatch_intent_id,
                        "attempt": row.attempt,
                        "worker_job_id": acceptance.worker_job_id,
                        "assignment_id": acceptance.assignment_id,
                    },
                },
            ]
            task.updated_at = now
            uow.session.add(row)
            uow.session.add(task)
        return self._response(row, replayed=False)

    def _record_failure(
        self,
        *,
        context: PlanningOperationContext,
        envelope: PlanningDispatchEnvelope,
        pump_owner: str,
        reason_code: str,
    ) -> dict[str, Any]:
        now = self._clock()
        with self._uow_factory() as uow:
            assert uow.session is not None
            row = self._scoped_dispatch(
                uow.session,
                context=context,
                dispatch_intent_id=envelope.dispatch_intent_id,
                for_update=True,
            )
            if row.status == "dispatched":
                return self._response(row, replayed=True)
            if row.status != "dispatching" or row.processing_owner != pump_owner or row.attempt != envelope.attempt:
                raise PlanningDispatchError("planning_dispatch_failure_stale")
            row.last_error_code = reason_code
            row.processing_owner = None
            row.processing_started_at = None
            row.processing_lease_expires_at = None
            row.updated_at = now
            if row.attempt >= self._max_attempts:
                row.status = "dispatch_failed"
                task = self._scoped_task(uow.session, context=context, dispatch=row)
                task.status = "paused"
                task.status_reason_code = "planning_dispatch_retry_exhausted"
                task.updated_at = now
                uow.session.add(task)
            else:
                row.status = "retry_pending"
                row.next_attempt_at = now + min(60.0, float(2 ** max(0, row.attempt - 1)))
            uow.session.add(row)
        return self._response(row, replayed=False)

    @staticmethod
    def _recover_acceptance(
        session: Session,
        *,
        row: PlanningTaskDispatchDB,
        task: TaskDB,
        now: float,
    ) -> bool:
        worker_job_id = str(task.current_worker_job_id or "").strip()
        if not worker_job_id:
            return False
        job = session.get(WorkerJobDB, worker_job_id)
        if (
            job is None
            or str(job.parent_task_id or "") != row.internal_task_id
            or not str(job.subtask_id or "")
            or not str(job.worker_url or "")
            or (
                bool(str(row.requested_worker_id or ""))
                and str(job.worker_url or "") != str(row.requested_worker_id or "")
            )
        ):
            raise PlanningDispatchError("planning_dispatch_recovery_receipt_invalid")
        if row.worker_job_id and row.worker_job_id != job.id:
            raise PlanningDispatchError("planning_dispatch_recovery_receipt_conflict")
        row.status = "dispatched"
        row.worker_job_id = job.id
        row.assignment_id = str(job.subtask_id)
        row.accepted_worker_id = str(job.worker_url)
        row.transport_receipt = {
            "status": "recovered_from_authoritative_worker_job",
            "worker_job_status": str(job.status or ""),
        }
        row.accepted_at = row.accepted_at or now
        row.updated_at = now
        row.processing_owner = None
        row.processing_started_at = None
        row.processing_lease_expires_at = None
        task.status = "in_progress"
        task.assigned_agent_url = str(job.worker_url)
        task.status_reason_code = "planning_dispatch_acceptance_recovered"
        task.worker_execution_context = {
            **dict(task.worker_execution_context or {}),
            "planning_dispatch": {
                **dict(dict(task.worker_execution_context or {}).get("planning_dispatch") or {}),
                "schema": "organization_planning_dispatch.v1",
                "dispatch_intent_id": row.dispatch_intent_id,
                "lease_id": row.lease_id,
                "attempt": row.attempt,
                "track_revision_id": row.track_revision_id,
                "status": "dispatched",
                "assignment_id": str(job.subtask_id),
                "worker_job_id": job.id,
                "worker_id": str(job.worker_url),
            },
        }
        task.history = [
            *list(task.history or []),
            {
                "timestamp": now,
                "status": "in_progress",
                "event_type": "organization_planning_dispatch_acceptance_recovered",
                "actor": "hub:planning_dispatch_outbox",
                "details": {
                    "dispatch_intent_id": row.dispatch_intent_id,
                    "attempt": row.attempt,
                    "worker_job_id": job.id,
                    "assignment_id": str(job.subtask_id),
                },
            },
        ]
        task.updated_at = now
        session.add(row)
        session.add(task)
        return True

    @staticmethod
    def _scoped_dispatch(
        session: Session,
        *,
        context: PlanningOperationContext,
        dispatch_intent_id: str,
        for_update: bool,
    ) -> PlanningTaskDispatchDB:
        statement = select(PlanningTaskDispatchDB).where(
            PlanningTaskDispatchDB.dispatch_intent_id == str(dispatch_intent_id or ""),
            PlanningTaskDispatchDB.tenant_id == context.tenant_id,
            PlanningTaskDispatchDB.project_id == context.project_id,
            PlanningTaskDispatchDB.organization_id == context.organization_id,
        )
        if for_update and PlanningDispatchOutboxService._supports_row_lock(session):
            statement = statement.with_for_update()
        row = session.exec(statement).one_or_none()
        if row is None:
            raise PlanningDispatchError("planning_dispatch_not_found")
        return row

    @staticmethod
    def _scoped_task(
        session: Session,
        *,
        context: PlanningOperationContext,
        dispatch: PlanningTaskDispatchDB,
    ) -> TaskDB:
        task = session.exec(
            select(TaskDB).where(
                TaskDB.id == dispatch.internal_task_id,
                TaskDB.tenant_id == context.tenant_id,
                TaskDB.project_id == context.project_id,
                TaskDB.organization_id == context.organization_id,
            )
        ).one_or_none()
        if task is None:
            raise PlanningDispatchError("planning_dispatch_task_not_found")
        if str(task.status or "") not in _ACTIVE_TASK_STATES:
            raise PlanningDispatchError("planning_dispatch_task_state_invalid")
        return task

    @staticmethod
    def _supports_row_lock(session: Session) -> bool:
        return str(getattr(getattr(session.get_bind(), "dialect", None), "name", "")) == "postgresql"

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningDispatchError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "track_dispatch" not in context.allowed_operations:
            raise PlanningDispatchError("planning_organization_admin_required")

    @staticmethod
    def _response(row: PlanningTaskDispatchDB, *, replayed: bool) -> dict[str, Any]:
        return {
            "dispatch_intent_id": row.dispatch_intent_id,
            "lease_id": row.lease_id,
            "track_revision_id": row.track_revision_id,
            "internal_task_id": row.internal_task_id,
            "attempt": row.attempt,
            "status": row.status,
            "worker_job_id": row.worker_job_id,
            "assignment_id": row.assignment_id,
            "worker_id": row.accepted_worker_id,
            "last_error_code": row.last_error_code,
            "next_attempt_at": row.next_attempt_at,
            "replayed": replayed,
        }


__all__ = [
    "HubTaskDelegationAdapter",
    "PlanningDispatchError",
    "PlanningDispatchOutboxService",
]
