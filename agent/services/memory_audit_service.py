"""Memory audit service.

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
class AuditEntry:
    run_id: str
    asset_id: str
    provider_id: Optional[str] = None  # unverified reference only
    status: str = "pending"
    created_at: str = field(default_factory=_utc_now)
    notes: Dict[str, Any] = field(default_factory=dict)


class MemoryAuditService:
    """Tracks Hub-owned audit entries for scoped runs."""

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []

    def record(self, entry: AuditEntry) -> AuditEntry:
        self._entries.append(entry)
        return entry

    def entries_for_run(self, run_id: str) -> List[AuditEntry]:
        return [e for e in self._entries if e.run_id == run_id]

    def retention_policy(self) -> Dict[str, Any]:
        # Central retention/privacy deletion is a remaining item; no local policy here.
        return {
            "scope": "hub", 
            "status": "pending_central_retention",
            "provider_workspace_lifetime": "temporary_per_run",
            "lineage_mirrors": "validated_outcome_pending",
        }
