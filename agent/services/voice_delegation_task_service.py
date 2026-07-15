from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.services.voice_governance_domain import VoicePrincipal, voice_scope_digest


@dataclass(frozen=True)
class VoiceDelegationTask:
    task_id: str
    deadline_epoch_ms: int


class VoiceDelegationTaskService:
    """Keep Hub queue ownership around one synchronous Voice delegation."""

    def __init__(self, *, audit_sink: Callable[[str, dict[str, Any]], None] = log_audit) -> None:
        self._audit = audit_sink

    def start(
        self,
        principal: VoicePrincipal,
        *,
        request_id: str,
        request_hash: str,
        effective_configuration: dict[str, Any],
        deadline_seconds: float,
        idempotency_key: str | None,
        deadline_epoch_ms: int | None = None,
        profile_id: str = "default",
        configuration_session_id: str | None = None,
        parent_task_id: str | None = None,
        operation: str = "transcribe",
    ) -> VoiceDelegationTask:
        from agent.services.task_queue_service import get_task_queue_service

        normalized_operation = str(operation or "transcribe").strip().lower()
        config_digest = hashlib.sha256(self._canonical(effective_configuration)).hexdigest()
        correlation_payload = {
            "tenant_id": principal.tenant_id,
            "owner_subject": principal.subject,
            "request_hash": request_hash,
            "config_digest": config_digest,
            "idempotency_key": idempotency_key or request_id,
            "profile_id": profile_id,
            "configuration_session_id": configuration_session_id,
        }
        if normalized_operation != "transcribe":
            correlation_payload["operation"] = normalized_operation
        correlation = self._canonical(correlation_payload)
        task_id = f"voice-transcription-{hashlib.sha256(correlation).hexdigest()[:32]}"
        now_epoch_ms = time.time_ns() // 1_000_000
        effective_deadline_epoch_ms = (
            int(deadline_epoch_ms)
            if deadline_epoch_ms is not None
            else now_epoch_ms + max(1, round(deadline_seconds * 1000))
        )
        if effective_deadline_epoch_ms <= now_epoch_ms:
            raise TimeoutError("voice delegation deadline expired before task creation")
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title=(
                "Hub-delegated Voice transcription"
                if normalized_operation == "transcribe"
                else f"Hub-delegated Voice {normalized_operation}"
            ),
            description="Execute one bounded local Voice runtime request.",
            priority="medium",
            created_by=principal.subject,
            source="voice_api",
            tags=(
                ["voice_transcription", "local_runtime"]
                if normalized_operation == "transcribe"
                else ["voice_transcription", f"voice_{normalized_operation}", "local_runtime"]
            ),
            event_type="voice_runtime_delegated",
            event_details={"request_id": request_id, "configuration_digest": config_digest},
            extra_fields={
                "task_kind": "voice_transcription",
                "parent_task_id": parent_task_id,
                "required_capabilities": ["voice_transcription"],
                "worker_execution_context": {
                    "voice_transcription": {
                        "request_id": request_id,
                        "request_digest": request_hash,
                        "configuration_digest": config_digest,
                        "profile_id": profile_id,
                        "deletion_scope_digest": voice_scope_digest(principal, profile_id),
                        "configuration_session_id": configuration_session_id,
                        **({"operation": normalized_operation} if normalized_operation != "transcribe" else {}),
                        "tenant_scope_hash": hashlib.sha256(principal.tenant_id.encode()).hexdigest(),
                        "owner_subject_hash": hashlib.sha256(principal.subject.encode()).hexdigest(),
                        "deadline_epoch_ms": effective_deadline_epoch_ms,
                        "persistence_owner": "hub",
                    }
                },
            },
        )
        self._audit(
            "voice_runtime_task_delegated",
            {
                "task_id": task_id,
                "request_id": request_id,
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "configuration_digest": config_digest,
            },
        )
        return VoiceDelegationTask(task_id=task_id, deadline_epoch_ms=effective_deadline_epoch_ms)

    def complete(self, task: VoiceDelegationTask, *, result_ref: str) -> None:
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        get_voice_task_terminal_service().update_existing(
            task.task_id,
            "completed",
            last_output=result_ref,
            verification_status={"voice_transcription": {"status": "verified", "result_ref": result_ref}},
            event_type="voice_runtime_completed",
            event_actor="hub",
            event_details={"result_ref": result_ref},
        )

    def fail(self, task: VoiceDelegationTask, exc: BaseException) -> None:
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        get_voice_task_terminal_service().update_existing(
            task.task_id,
            "failed",
            status_reason_code="voice_runtime_failed",
            status_reason_details={"error_type": type(exc).__name__},
            event_type="voice_runtime_failed",
            event_actor="hub",
            event_details={"error_type": type(exc).__name__},
        )

    def cancel(self, task_id: str, *, reason_code: str) -> None:
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        get_voice_task_terminal_service().update_existing(
            task_id,
            "cancelled",
            status_reason_code=reason_code,
            status_reason_details={},
            event_type="voice_runtime_cancelled",
            event_actor="hub",
            event_details={"reason_code": reason_code},
        )

    @staticmethod
    def remaining_seconds(task: VoiceDelegationTask) -> float:
        return max(0.0, (task.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0)

    @staticmethod
    def _canonical(payload: Any) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


voice_delegation_task_service = VoiceDelegationTaskService()


def get_voice_delegation_task_service() -> VoiceDelegationTaskService:
    return voice_delegation_task_service
