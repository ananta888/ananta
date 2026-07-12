from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.voice_governance_domain import VoicePrincipal


@dataclass(frozen=True)
class RestrictedManagementTaskResult:
    task_id: str
    payload: dict[str, Any]


class RestrictedInferenceManagementTaskService:
    """Hub queue owner for one bounded worker-management command."""

    def execute(
        self,
        principal: VoicePrincipal,
        *,
        operation: str,
        target_id: str,
        request_id: str,
        callback: Callable[[], dict[str, Any]],
    ) -> RestrictedManagementTaskResult:
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.task_runtime_service import update_local_task_status

        identity = self._canonical(
            {
                "operation": operation,
                "request_id": request_id,
                "target_id": target_id,
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
            }
        )
        task_id = f"restricted-management-{hashlib.sha256(identity).hexdigest()[:32]}"
        target_digest = hashlib.sha256(target_id.encode()).hexdigest()
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title=f"Restricted inference management: {operation}",
            description="Execute one Hub-authorized bounded worker management operation.",
            priority="high",
            created_by=principal.subject,
            source="restricted_inference_management",
            tags=["restricted_inference", "management", "no_generation"],
            event_type="restricted_management_delegated",
            event_details={"operation": operation, "target_digest": target_digest},
            extra_fields={
                "task_kind": "restricted_inference_management",
                "required_capabilities": ["restricted_inference_management", operation],
                "worker_execution_context": {
                    "restricted_inference_management": {
                        "operation": operation,
                        "target_digest": target_digest,
                        "persistence_owner": "hub",
                        "no_generation": True,
                    }
                },
            },
        )
        try:
            payload = callback()
        except Exception as exc:
            update_local_task_status(
                task_id,
                "failed",
                status_reason_code="restricted_management_failed",
                status_reason_details={"error_type": type(exc).__name__},
                event_type="restricted_management_failed",
                event_actor="hub",
                event_details={"operation": operation, "error_type": type(exc).__name__},
            )
            raise
        update_local_task_status(
            task_id,
            "completed",
            verification_status={
                "restricted_inference_management": {
                    "status": "verified",
                    "operation": operation,
                    "no_generation": True,
                }
            },
            event_type="restricted_management_completed",
            event_actor="hub",
            event_details={"operation": operation, "target_digest": target_digest},
        )
        return RestrictedManagementTaskResult(task_id=task_id, payload=dict(payload))

    @staticmethod
    def _canonical(value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


restricted_inference_management_task_service = RestrictedInferenceManagementTaskService()


def get_restricted_inference_management_task_service() -> RestrictedInferenceManagementTaskService:
    return restricted_inference_management_task_service
