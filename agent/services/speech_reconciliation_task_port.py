"""Hub-owned task projection for offline speech reconciliation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ananta_contracts.speech_reconciliation import SpeechReconciliationJob, canonical_sha256


@dataclass(frozen=True, slots=True)
class SpeechReconciliationTaskReference:
    parent_task_id: str
    attempt_task_id: str | None
    status: str


class SpeechReconciliationTaskQueuePort(Protocol):
    def ingest_task(self, **values: Any) -> None: ...


class SpeechReconciliationTerminalPort(Protocol):
    def cancel(self, task_id: str, *, reason_code: str) -> None: ...


class SpeechReconciliationTaskStatusPort(Protocol):
    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class SpeechReconciliationTaskPort(Protocol):
    def materialize_parent(
        self,
        job: SpeechReconciliationJob,
        *,
        tenant_id: str,
        owner_subject: str,
    ) -> SpeechReconciliationTaskReference: ...

    def enqueue_attempt(
        self,
        job: SpeechReconciliationJob,
        *,
        tenant_id: str,
        owner_subject: str,
        worker_id: str,
        worker_location: str,
        resource_profile: Mapping[str, int],
        checkpoint_ref: str | None,
    ) -> SpeechReconciliationTaskReference: ...

    def cancel(self, task_id: str, *, reason_code: str) -> None: ...

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class HubSpeechReconciliationTaskPort:
    """Projects parent/attempt tasks without granting workers orchestration."""

    def __init__(
        self,
        queue: SpeechReconciliationTaskQueuePort | None = None,
        terminal: SpeechReconciliationTerminalPort | SpeechReconciliationTaskStatusPort | None = None,
    ) -> None:
        self._queue = queue
        self._terminal = terminal

    def materialize_parent(
        self,
        job: SpeechReconciliationJob,
        *,
        tenant_id: str,
        owner_subject: str,
    ) -> SpeechReconciliationTaskReference:
        parent_id = self.parent_task_id(job.job_id)
        self._ingest(
            task_id=parent_id,
            status="queued",
            title="Hub-controlled speech reconciliation",
            description="Coordinate one bounded offline speech reconciliation job.",
            priority="low",
            created_by="hub",
            source="speech_reconciliation",
            tags=["speech_reconciliation", "offline", "hub_parent"],
            event_type="speech_reconciliation_parent_created",
            event_details={"job_id": job.job_id, "stage": job.stage},
            extra_fields={
                "task_kind": "speech_reconciliation_parent",
                "worker_execution_context": {
                    "speech_reconciliation": {
                        "job_id": job.job_id,
                        "binding_digest": canonical_sha256(job.to_dict()),
                        "persistence_owner": "hub",
                        "followup_task_creation_allowed": False,
                        **self._scope(tenant_id, owner_subject),
                    }
                },
            },
        )
        return SpeechReconciliationTaskReference(parent_id, None, "queued")

    def enqueue_attempt(
        self,
        job: SpeechReconciliationJob,
        *,
        tenant_id: str,
        owner_subject: str,
        worker_id: str,
        worker_location: str,
        resource_profile: Mapping[str, int],
        checkpoint_ref: str | None,
    ) -> SpeechReconciliationTaskReference:
        parent_id = self.parent_task_id(job.job_id)
        attempt_id = self.attempt_task_id(job.job_id, job.attempt_id, job.fencing_epoch)
        context = {
            "job": job.to_dict(),
            "resource_profile": {str(key): int(value) for key, value in resource_profile.items()},
            "worker_location": worker_location,
            "checkpoint_ref": checkpoint_ref,
            "persistence_owner": "hub",
            "followup_task_creation_allowed": False,
            "peer_transfer_allowed": False,
            "training_delegation_allowed": False,
            **self._scope(tenant_id, owner_subject),
        }
        self._ingest(
            task_id=attempt_id,
            status="assigned",
            title="Hub-delegated speech reconciliation attempt",
            description="Execute exactly one fenced offline reconciliation attempt.",
            priority="low",
            created_by="hub",
            source="speech_reconciliation",
            tags=["speech_reconciliation", "offline", "hub_child"],
            event_type="speech_reconciliation_attempt_delegated",
            event_details={
                "job_id": job.job_id,
                "attempt_id": job.attempt_id,
                "fencing_epoch": job.fencing_epoch,
            },
            extra_fields={
                "task_kind": "speech_reconciliation_attempt",
                "parent_task_id": parent_id,
                "assigned_agent_url": worker_id,
                "required_capabilities": ["speech_reconciliation"],
                "worker_execution_context": {"speech_reconciliation": context},
            },
        )
        return SpeechReconciliationTaskReference(parent_id, attempt_id, "assigned")

    def cancel(self, task_id: str, *, reason_code: str) -> None:
        self.finish(task_id, status="cancelled", reason_code=reason_code)

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        if not reason_code or len(reason_code) > 128 or any(character.isspace() for character in reason_code):
            raise ValueError("speech_reconciliation_cancel_reason_invalid")
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("speech_reconciliation_terminal_status_invalid")
        if self._terminal is not None:
            finish = getattr(self._terminal, "finish", None)
            if callable(finish):
                finish(task_id, status=status, reason_code=reason_code)
                return
            if status == "cancelled":
                self._terminal.cancel(task_id, reason_code=reason_code)
                return
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            status,
            status_reason_code=reason_code,
            status_reason_details={},
            event_type=f"speech_reconciliation_{status}",
            event_actor="hub",
            event_details={"reason_code": reason_code},
        )

    @staticmethod
    def parent_task_id(job_id: str) -> str:
        return f"speech-reconciliation-{hashlib.sha256(job_id.encode()).hexdigest()[:32]}"

    @staticmethod
    def attempt_task_id(job_id: str, attempt_id: str, fencing_epoch: int) -> str:
        digest = hashlib.sha256(f"{job_id}:{attempt_id}:{fencing_epoch}".encode()).hexdigest()
        return f"speech-reconciliation-attempt-{digest[:32]}"

    @staticmethod
    def _scope(tenant_id: str, owner_subject: str) -> dict[str, str]:
        return {
            "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
            "owner_subject_hash": hashlib.sha256(owner_subject.encode()).hexdigest(),
        }

    def _ingest(self, **values: Any) -> None:
        queue = self._queue
        if queue is None:
            from agent.services.task_queue_service import get_task_queue_service

            queue = get_task_queue_service()
        queue.ingest_task(**values)


__all__ = [
    "HubSpeechReconciliationTaskPort",
    "SpeechReconciliationTaskPort",
    "SpeechReconciliationTaskQueuePort",
    "SpeechReconciliationTaskReference",
    "SpeechReconciliationTaskStatusPort",
    "SpeechReconciliationTerminalPort",
]
