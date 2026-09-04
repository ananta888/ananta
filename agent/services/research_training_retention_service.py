"""Reference-safe filesystem cleanup for quota-selected research artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_quota_service import ResearchTrainingQuotaService


class ResearchTrainingRetentionService:
    def __init__(
        self,
        root: str | Path,
        quota: ResearchTrainingQuotaService,
        lineage: ResearchTrainingLineageService,
    ) -> None:
        self._root = Path(root).resolve()
        self._quota = quota
        self._lineage = lineage

    def collect(self, *, tenant_id: str, referenced_digests: list[str], limit: int = 100) -> dict[str, Any]:
        deleted: list[str] = []
        rejected: list[str] = []
        authoritative_references = sorted(
            set(referenced_digests) | set(self._lineage.referenced_digests(tenant_id=tenant_id))
        )
        for candidate in self._quota.garbage_candidates(
            tenant_id=tenant_id, referenced_digests=authoritative_references, limit=limit
        ):
            target = (self._root / candidate["artifact_ref"]).resolve()
            if self._root not in target.parents or not target.is_file() or target.is_symlink():
                rejected.append(candidate["artifact_digest"])
                continue
            content = target.read_bytes()
            if (
                len(content) != candidate["size_bytes"]
                or hashlib.sha256(content).hexdigest() != candidate["artifact_digest"]
            ):
                rejected.append(candidate["artifact_digest"])
                continue
            try:
                self._lineage.delete_leaf(
                    tenant_id=tenant_id,
                    artifact_digest=candidate["artifact_digest"],
                )
            except (KeyError, ValueError):
                rejected.append(candidate["artifact_digest"])
                continue
            target.unlink()
            self._quota.forget(tenant_id=tenant_id, artifact_digest=candidate["artifact_digest"])
            deleted.append(candidate["artifact_digest"])
        return {
            "schema": "ananta.research-training-retention-result.v1",
            "deleted_digests": deleted,
            "rejected_digests": rejected,
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingRetentionService"]
