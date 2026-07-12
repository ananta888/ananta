from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent.common.audit import log_audit
from agent.repositories.voice_deletion_reconciliation import (
    VoiceDeletionCandidateScope,
    VoiceDeletionReconciliationRepository,
)
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.services.voice_governance_domain import VoicePrincipal, voice_scope_digest
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupService,
    get_voice_runtime_cleanup_service,
)


@dataclass(frozen=True)
class VoiceDeletionReconciliationRun:
    reconciled_scope_count: int
    deleted_record_count: int


class VoiceDeletionReconciliationService:
    """Reapply durable full-deletion intent after backup restoration."""

    def __init__(
        self,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        reconciliation: VoiceDeletionReconciliationRepository | None = None,
        runtime_cleanup: VoiceRuntimeCleanupService | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._reconciliation = reconciliation or VoiceDeletionReconciliationRepository()
        self._runtime_cleanup = runtime_cleanup or get_voice_runtime_cleanup_service()
        self._audit = audit_sink

    def reconcile_all(self, *, page_size: int = 500) -> VoiceDeletionReconciliationRun:
        self._tombstones.sync_from_ledger()
        candidates: dict[str, VoiceDeletionCandidateScope] = {}
        for discovered in self._reconciliation.list_candidate_scopes():
            digest = voice_scope_digest(discovered.principal, discovered.profile_id)
            if digest in candidates:
                raise RuntimeError("voice deletion scope digest collision")
            candidates[digest] = discovered
        reconciled_scope_count = 0
        deleted_record_count = 0
        cursor: tuple[float, str] | None = None
        while True:
            page = self._tombstones.list_page(after=cursor, limit=page_size)
            if not page:
                break
            for tombstone in page:
                candidate = candidates.get(tombstone.scope_digest)
                digest_task_counts = self._reconciliation.delete_tasks_by_scope_digest(
                    tombstone.scope_digest,
                    deleted_at=tombstone.deleted_at,
                )
                deleted_count = sum(digest_task_counts.values())
                cleanup_principal = VoicePrincipal(
                    tenant_id=f"privacy-reconcile-{tombstone.scope_digest[:24]}",
                    subject="hub-privacy-cleanup",
                )
                self._runtime_cleanup.stage_cache_gc(
                    cleanup_principal,
                    profile_id=f"scope-{tombstone.scope_digest[:24]}",
                    operation="profile_delete",
                )
                if candidate is not None:
                    counts = self._reconciliation.delete_before(
                        candidate.principal,
                        candidate.profile_id,
                        deleted_at=tombstone.deleted_at,
                    )
                    deleted_count += sum(counts.values())
                if not self._tombstones.mark_reconciled(tombstone.scope_digest):
                    raise RuntimeError("voice deletion tombstone disappeared during reconciliation")
                reconciled_scope_count += 1
                deleted_record_count += deleted_count
                self._audit(
                    "voice_deletion_reconciled",
                    {
                        "scope_digest": tombstone.scope_digest,
                        "deleted_count": deleted_count,
                        "status": "reconciled",
                    },
                )
            last = page[-1]
            cursor = (last.deleted_at, last.id)
        return VoiceDeletionReconciliationRun(
            reconciled_scope_count=reconciled_scope_count,
            deleted_record_count=deleted_record_count,
        )


voice_deletion_reconciliation_service = VoiceDeletionReconciliationService()


def get_voice_deletion_reconciliation_service() -> VoiceDeletionReconciliationService:
    return voice_deletion_reconciliation_service
