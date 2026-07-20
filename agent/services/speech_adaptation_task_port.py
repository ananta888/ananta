"""Hub-owned task queue adapter for speech adaptation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.speech_adaptation import SpeechAdaptationJob


@dataclass(frozen=True)
class SpeechAdaptationTaskReference:
    task_id: str
    status: str


class SpeechAdaptationTaskPort(Protocol):
    def enqueue(
        self,
        job: SpeechAdaptationJob,
        *,
        tenant_id: str,
        owner_subject: str,
    ) -> SpeechAdaptationTaskReference: ...

    def enqueue_policy_state(
        self,
        *,
        job_id: str,
        tenant_id: str,
        owner_subject: str,
        status: str,
        reason_code: str,
        binding_digest: str,
    ) -> SpeechAdaptationTaskReference: ...

    def cancel(self, task_id: str, *, reason_code: str) -> None: ...

    def mark_running(self, task_id: str, *, job: SpeechAdaptationJob) -> None: ...

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class HubSpeechAdaptationTaskPort:
    """Keeps queue ownership and lifecycle mutation inside the Hub."""

    def enqueue(self, job: SpeechAdaptationJob, *, tenant_id: str, owner_subject: str) -> SpeechAdaptationTaskReference:
        task_id = f"speech-adaptation-{hashlib.sha256(job.job_id.encode()).hexdigest()[:32]}"
        self._ingest(
            task_id=task_id,
            job_id=job.job_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            status="queued",
            reason_code="speech_training_admitted",
            binding_digest=job.binding_digest,
            attempt_id=job.attempt.attempt_id,
            fencing_digest=job.fencing.fencing_digest,
        )
        return SpeechAdaptationTaskReference(task_id=task_id, status="queued")

    def enqueue_policy_state(
        self,
        *,
        job_id: str,
        tenant_id: str,
        owner_subject: str,
        status: str,
        reason_code: str,
        binding_digest: str,
    ) -> SpeechAdaptationTaskReference:
        if status not in {"queued", "dataset_only", "denied"}:
            raise ValueError("speech admission policy status is invalid")
        task_id = f"speech-adaptation-{hashlib.sha256(job_id.encode()).hexdigest()[:32]}"
        self._ingest(
            task_id=task_id,
            job_id=job_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            status="queued" if status == "queued" else "completed" if status == "dataset_only" else "cancelled",
            reason_code=reason_code,
            binding_digest=binding_digest,
            attempt_id=None,
            fencing_digest=None,
        )
        return SpeechAdaptationTaskReference(task_id=task_id, status=status)

    def cancel(self, task_id: str, *, reason_code: str) -> None:
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        get_voice_task_terminal_service().update_existing(
            task_id,
            "cancelled",
            status_reason_code=reason_code,
            status_reason_details={},
            event_type="speech_adaptation_cancelled",
            event_actor="hub",
            event_details={"reason_code": reason_code},
        )

    def mark_running(self, task_id: str, *, job: SpeechAdaptationJob) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            "in_progress",
            event_type="speech_adaptation_worker_started",
            event_actor="hub",
            event_details={
                "job_id": job.job_id,
                "attempt_id": job.attempt.attempt_id,
                "binding_digest": job.binding_digest,
            },
        )

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        if status not in {"completed", "dataset_only", "cancelled", "failed"}:
            raise ValueError("speech adaptation terminal task status is invalid")
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        task_status = "completed" if status == "dataset_only" else status
        get_voice_task_terminal_service().update_existing(
            task_id,
            task_status,
            status_reason_code=reason_code,
            status_reason_details={},
            event_type="speech_adaptation_finished",
            event_actor="hub",
            event_details={"reason_code": reason_code, "result_status": status},
        )

    @staticmethod
    def _ingest(
        *,
        task_id: str,
        job_id: str,
        tenant_id: str,
        owner_subject: str,
        status: str,
        reason_code: str,
        binding_digest: str,
        attempt_id: str | None,
        fencing_digest: str | None,
    ) -> None:
        from agent.services.task_queue_service import get_task_queue_service

        scope = {
            "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
            "owner_subject_hash": hashlib.sha256(owner_subject.encode()).hexdigest(),
        }
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status=status,
            title="Hub-controlled speech adaptation",
            description="Execute one admitted, bounded and fenced local speech adaptation job.",
            priority="high",
            created_by=owner_subject,
            source="speech_adaptation",
            tags=["speech_adaptation", "local_training", "hub_orchestration"],
            event_type="speech_adaptation_admitted",
            event_details={"job_id": job_id, "reason_code": reason_code},
            extra_fields={
                "task_kind": "speech_adaptation",
                "required_capabilities": ["speech_adaptation_training"],
                "worker_execution_context": {
                    "speech_adaptation": {
                        "job_id": job_id,
                        "attempt_id": attempt_id,
                        "binding_digest": binding_digest,
                        "fencing_digest": fencing_digest,
                        "persistence_owner": "hub",
                        "followup_task_creation_allowed": False,
                        **scope,
                    }
                },
            },
        )
