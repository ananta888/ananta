"""Atomic admission boundary for assignment-bound Worker callbacks."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session, select

from agent.common.task_mutation_lock import get_task_mutation_lock_port
from agent.database import engine
from agent.db_models import TaskDB, WorkerJobDB, WorkerSlotLeaseDB


class WorkerResultCallbackError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class WorkerResultCallbackService:
    """Admit one exact callback under the current Hub dispatch lease.

    The receipt is stored on the authoritative WorkerJob.  It therefore
    survives process restarts and makes an exact HTTP retry harmless while a
    changed payload or second capability for the same dispatch fails closed.
    """

    _RECEIPT_KEY = "worker_result_callback_receipt"
    _RECEIPT_SCHEMA = "worker_result_callback_receipt.v1"
    _RESULT_STATUSES = frozenset({"completed", "failed", "cancelled", "verification_failed"})
    _TERMINAL_TASK_STATUSES = frozenset(
        {
            "completed",
            "failed",
            "cancelled",
            "verification_failed",
            "skipped",
            "aborted",
            "timeout",
            "archived",
        }
    )

    def accept(
        self,
        *,
        task_id: str,
        payload: Mapping[str, Any],
        capability_claims: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        assignment_id = str(payload.get("id") or payload.get("assignment_id") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        if not normalized_task_id or not assignment_id or status not in self._RESULT_STATUSES:
            raise WorkerResultCallbackError("worker_result_callback_payload_invalid")

        digest = self._payload_digest(payload)
        lease_id = str(capability_claims.get("dispatch_lease_id") or "").strip()
        worker_id = str(capability_claims.get("worker_id") or "").strip()
        jti = str(capability_claims.get("jti") or "").strip()
        scopes = {str(value) for value in list(capability_claims.get("scopes") or [])}
        if (
            str(capability_claims.get("source_task_id") or "") != normalized_task_id
            or str(capability_claims.get("assignment_id") or "") != assignment_id
            or not lease_id
            or not worker_id
            or not jti
            or "worker.result.submit" not in scopes
        ):
            raise WorkerResultCallbackError("worker_result_callback_capability_invalid")

        with get_task_mutation_lock_port().mutation_locks({normalized_task_id}) as acquired:
            if not acquired:
                raise WorkerResultCallbackError("worker_result_callback_lock_unavailable")
            with Session(engine) as session:
                task = self._locked_row(session, TaskDB, normalized_task_id)
                job = self._locked_row(session, WorkerJobDB, lease_id)
                if task is None or job is None:
                    raise WorkerResultCallbackError("worker_result_callback_assignment_invalid")

                metadata = dict(job.job_metadata or {})
                receipt = metadata.get(self._RECEIPT_KEY)
                if isinstance(receipt, Mapping):
                    if self._receipt_matches(
                        receipt,
                        task_id=normalized_task_id,
                        assignment_id=assignment_id,
                        lease_id=lease_id,
                        worker_id=worker_id,
                        jti=jti,
                        payload_digest=digest,
                    ):
                        return {"status": "updated", "replayed": True}
                    raise WorkerResultCallbackError("worker_result_callback_idempotency_conflict")

                slot_lease = self._require_live_binding(
                    session,
                    task=task,
                    job=job,
                    assignment_id=assignment_id,
                    lease_id=lease_id,
                    worker_id=worker_id,
                )
                result_projection: dict[str, Any] = {
                    "status": status,
                    "worker_job_id": lease_id,
                }
                if "last_output" in payload:
                    result_projection["last_output"] = payload["last_output"]
                if "last_exit_code" in payload:
                    result_projection["last_exit_code"] = payload["last_exit_code"]
                if isinstance(payload.get("artifacts"), list):
                    result_projection["artifacts"] = list(payload.get("artifacts") or [])
                worker_context = dict(task.worker_execution_context or {})
                subtask_results = dict(worker_context.get("subtask_results") or {})
                subtask_results[assignment_id] = result_projection
                worker_context["subtask_results"] = subtask_results
                task.worker_execution_context = worker_context
                task.updated_at = time.time()

                metadata[self._RECEIPT_KEY] = {
                    "schema": self._RECEIPT_SCHEMA,
                    "task_id": normalized_task_id,
                    "assignment_id": assignment_id,
                    "dispatch_lease_id": lease_id,
                    "worker_id": worker_id,
                    "capability_jti": jti,
                    "payload_digest": digest,
                    "accepted_at": time.time(),
                }
                job.job_metadata = metadata
                accepted_at = time.time()
                job.status = status if status in {"completed", "failed", "cancelled"} else "failed"
                job.finished_at = accepted_at
                job.updated_at = accepted_at
                session.add(task)
                session.add(job)
                if slot_lease is not None:
                    slot_lease.status = "released"
                    slot_lease.released_at = accepted_at
                    session.add(slot_lease)
                session.commit()

        try:
            from agent.services.task_runtime_service import notify_task_update

            notify_task_update(normalized_task_id)
        except Exception:
            pass
        return {"status": "updated", "replayed": False}

    @staticmethod
    def _locked_row(session: Session, model, row_id: str):
        statement = select(model).where(model.id == row_id)
        if str(engine.dialect.name or "").lower() == "postgresql":
            statement = statement.with_for_update()
        return session.exec(statement).one_or_none()

    @staticmethod
    def _require_live_binding(
        session: Session,
        *,
        task: TaskDB,
        job: WorkerJobDB,
        assignment_id: str,
        lease_id: str,
        worker_id: str,
    ) -> WorkerSlotLeaseDB | None:
        raw_binding = dict(task.worker_execution_context or {}).get("task_proposal_binding")
        binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
        if (
            str(task.current_worker_job_id or "") != lease_id
            or str(task.status or "").strip().lower() in WorkerResultCallbackService._TERMINAL_TASK_STATUSES
            or str(job.parent_task_id or "") != str(task.id or "")
            or str(job.subtask_id or "") != assignment_id
            or str(job.worker_url or "") != worker_id
            or str(job.status or "") not in {"delegated", "running"}
            or job.finished_at is not None
            or str(binding.get("assignment_id") or "") != assignment_id
            or str(binding.get("dispatch_lease_id") or "") != lease_id
            or str(binding.get("worker_id") or "") != worker_id
        ):
            raise WorkerResultCallbackError("worker_result_callback_dispatch_lease_inactive")

        slot_lease = session.get(WorkerSlotLeaseDB, str(job.slot_lease_id or "")) if job.slot_lease_id else None
        if job.slot_lease_id and slot_lease is None:
            raise WorkerResultCallbackError("worker_result_callback_dispatch_lease_inactive")
        if slot_lease is not None and (
            str(slot_lease.status or "") != "active"
            or float(slot_lease.deadline_at) <= time.time()
            or slot_lease.released_at is not None
            or str(slot_lease.parent_task_id or "") not in {"", str(task.id or "")}
            or str(slot_lease.worker_job_id or "") not in {"", lease_id}
        ):
            raise WorkerResultCallbackError("worker_result_callback_dispatch_lease_inactive")
        return slot_lease

    @classmethod
    def _receipt_matches(
        cls,
        receipt: Mapping[str, Any],
        **expected: str,
    ) -> bool:
        return receipt.get("schema") == cls._RECEIPT_SCHEMA and all(
            str(receipt.get(field) or "") == value
            for field, value in (
                ("task_id", expected["task_id"]),
                ("assignment_id", expected["assignment_id"]),
                ("dispatch_lease_id", expected["lease_id"]),
                ("worker_id", expected["worker_id"]),
                ("capability_jti", expected["jti"]),
                ("payload_digest", expected["payload_digest"]),
            )
        )

    @staticmethod
    def _payload_digest(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["WorkerResultCallbackError", "WorkerResultCallbackService"]
