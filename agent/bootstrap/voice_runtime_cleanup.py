from __future__ import annotations

from agent.config import settings
from agent.services.voice_deletion_reconciliation_service import get_voice_deletion_reconciliation_service
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_runtime_cleanup_service import get_voice_runtime_cleanup_service


def recover_voice_runtime_cleanup() -> None:
    """Replay durable runtime cleanup only in the Hub control plane."""

    if settings.role != "hub":
        return
    get_voice_deletion_reconciliation_service().reconcile_all()
    # Provisional records represent Runtime capabilities that were active in a
    # previous Hub process. No in-memory mapping can survive this restart, so
    # they must be deleted before the Hub accepts new Voice work.
    get_voice_runtime_cleanup_service().retry_all_pending(include_provisional=True)
    lost_streams = VoiceIdempotencyService().invalidate_completed_operation(
        "voice_stream.create"
    )
    if lost_streams:
        from agent.repository import task_repo
        from agent.services.voice_delegation_task_service import (
            get_voice_delegation_task_service,
        )

        delegation = get_voice_delegation_task_service()
        for metadata in lost_streams:
            task_id = str(metadata.get("task_id") or "")
            task = task_repo.get_by_id(task_id) if task_id else None
            if task is not None and task.status not in {
                "completed",
                "failed",
                "cancelled",
                "archived",
            }:
                delegation.cancel(task_id, reason_code="voice_stream_hub_restarted")
