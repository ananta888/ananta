from __future__ import annotations

import time
from collections.abc import Callable

from agent.common.audit import log_audit
from agent.repositories.voice_live_runs import VoiceLiveRunRepository
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_preview_service import (
    VoiceLiveRunPreviewService,
    get_voice_live_run_preview_service,
)
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort


class VoiceLiveRunMaintenanceService:
    """Hub-owned state reconciliation for abandoned long Voice runs."""

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository | None = None,
        tasks: VoiceLiveRunTaskPort | None = None,
        previews: VoiceLiveRunPreviewService | None = None,
        clock: Callable[[], float] = time.time,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoiceLiveRunRepository()
        self._tasks = tasks or VoiceLiveRunTaskPort()
        self._previews = previews or get_voice_live_run_preview_service()
        self._clock = clock
        self._audit = audit_sink

    def run_once(self, *, limit: int = 500) -> dict[str, int]:
        now = self._clock()
        runs = self._repository.claim_expired_runs(
            now=now,
            limit=limit,
        )
        cancelled_segments = 0
        reconciled_runs = 0
        failed_runs = 0
        for run in runs:
            principal = VoicePrincipal(
                tenant_id=run.tenant_id,
                subject=run.owner_subject,
            )
            lease_token = str(run.maintenance_lease_token or "")
            try:
                self._previews.cleanup_run(
                    principal,
                    run.id,
                    reason_code="voice_live_preview_run_expired",
                )
                for segment in self._repository.list_segments(principal, run.id):
                    if segment.failure_code == "run_expired" and segment.task_id:
                        self._tasks.cancel_child(
                            segment.task_id,
                            reason_code="voice_live_run_expired",
                        )
                        cancelled_segments += 1
                    if (
                        segment.correction_failure_code == "run_expired"
                        and segment.correction_task_id
                    ):
                        self._tasks.cancel_child(
                            segment.correction_task_id,
                            reason_code="voice_live_run_expired",
                        )
                        cancelled_segments += 1
                self._tasks.expire_parent(run)
                if not lease_token or not self._repository.complete_expiry_reconciliation(
                    run.id,
                    lease_token=lease_token,
                    now=now,
                ):
                    raise RuntimeError("voice live-run maintenance lease was lost")
                reconciled_runs += 1
            except Exception:
                failed_runs += 1
                if lease_token:
                    self._repository.release_expiry_reconciliation(
                        run.id,
                        lease_token=lease_token,
                    )
        counts = {
            "expired_runs": reconciled_runs,
            "cancelled_segment_tasks": cancelled_segments,
            "failed_runs": failed_runs,
        }
        self._audit(
            "voice_live_run_maintenance_completed",
            {
                "status": "completed",
                **counts,
            },
        )
        return counts


voice_live_run_maintenance_service = VoiceLiveRunMaintenanceService()


def get_voice_live_run_maintenance_service() -> VoiceLiveRunMaintenanceService:
    return voice_live_run_maintenance_service
