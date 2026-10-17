"""HRM experiments artifact store.

Canonical ownership (per operations runbook):
- Hub owns scoped runs, assets, and audit records.
- Provider IDs are unverified references only.
- Per-run provider workspaces are temporary.

Remaining work: validated outcome/lineage mirrors, central retention/privacy deletion,
historical cross-engine reconstruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ArtifactRecord:
    run_id: str
    asset_id: str
    provider_id: Optional[str] = None  # unverified reference only
    created_at: str = field(default_factory=_utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ArtifactStore:
    """In-memory store for Hub-owned scoped run artifacts."""

    def __init__(self) -> None:
        self._records: List[ArtifactRecord] = []

    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        self._records.append(record)
        return record

    def list_for_run(self, run_id: str) -> List[ArtifactRecord]:
        return [r for r in self._records if r.run_id == run_id]

    def audit_summary(self, run_id: str) -> Dict[str, Any]:
        records = self.list_for_run(run_id)
        return {
            "run_id": run_id,
            "count": len(records),
            "provider_ids_unverified": [r.provider_id for r in records if r.provider_id],
            "created_at_max": max((r.created_at for r in records), default=None),
        }
