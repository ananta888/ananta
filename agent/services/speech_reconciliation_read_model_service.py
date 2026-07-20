"""Content-free projection for speech-reconciliation API and UI clients."""

from __future__ import annotations

from typing import Callable

from agent.repositories.speech_reconciliation import (
    SpeechReconciliationJobRecord,
    SqlSpeechReconciliationBudgetRepository,
)
from ananta_contracts.speech_reconciliation import SpeechReconciliationBudgetLedger


class SpeechReconciliationReadModelService:
    def __init__(
        self,
        budget_lookup: Callable[[str, str, str], SpeechReconciliationBudgetLedger | None] | None = None,
    ) -> None:
        self._budget_lookup = budget_lookup or self._sql_budget

    def project(self, job: SpeechReconciliationJobRecord) -> dict[str, object]:
        budget = self._budget_lookup(job.tenant_id, job.owner_subject, job.id)
        persisted_plan = dict(job.budget_plan or {})
        fallback_allocated = persisted_plan.get("allocated")
        return {
            "job_id": job.id,
            "state": job.state,
            "stage": job.stage,
            "reason_code": job.reason_code,
            "source_duration_ms": job.source_duration_ms,
            "max_compute_factor": job.max_compute_factor,
            "current_compute_factor": job.current_compute_factor,
            "quality_wave_count": len(job.quality_history),
            "training_budget_configured": job.training_budget is not None,
            "ledger_sequence": job.ledger_sequence,
            "key_epoch": job.key_epoch,
            "checkpoint_count": job.checkpoint_count,
            "conflict_counts": {
                "resolved": job.resolved_count,
                "unresolved": job.unresolved_count,
                "rejected": job.rejected_count,
                "quarantined": job.quarantined_count,
            },
            "budget": (
                {
                    "allocated": budget.allocated.to_dict(),
                    "reserved": budget.reserved.to_dict(),
                    "consumed": budget.consumed.to_dict(),
                    "remaining": budget.remaining.to_dict(),
                }
                if budget is not None
                else (
                    {
                        "allocated": dict(fallback_allocated),
                        "reserved": _zero_vector(),
                        "consumed": _zero_vector(),
                        "remaining": dict(fallback_allocated),
                    }
                    if isinstance(fallback_allocated, dict)
                    else None
                )
            ),
            "active_attempt_id": job.active_attempt_id,
            "version": job.version,
            "created_at_ms": job.created_at_ms,
            "updated_at_ms": job.updated_at_ms,
            "finished_at_ms": job.finished_at_ms,
        }

    @staticmethod
    def _sql_budget(tenant_id: str, owner_subject: str, job_id: str):
        return SqlSpeechReconciliationBudgetRepository(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        ).get(job_id=job_id)


def _zero_vector() -> dict[str, int]:
    from ananta_contracts.speech_reconciliation import RESOURCE_FIELDS

    return {field: 0 for field in RESOURCE_FIELDS}


__all__ = ["SpeechReconciliationReadModelService"]
