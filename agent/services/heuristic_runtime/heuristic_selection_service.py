"""HeuristicSelectionService — AI/ananta-worker control-path for heuristic selection.

Worker POST /heuristic/select → HeuristicSelectionRequest → HeuristicSelectionResponse.
Validated selection is persisted as a HeuristicDecisionLease.

Worker role constraint: opencode is NEVER the control worker (validated via
WorkerRoleConfigService).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.db_models import HeuristicDecisionLeaseDB
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository, _DOMAIN_TTL_DEFAULTS
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry, get_heuristic_registry
from agent.services.worker_role_config_service import WorkerRoleConfigService


@dataclass
class HeuristicSelectionRequest:
    domain: str
    context_hash: str
    available_heuristic_ids: list[str]
    current_lease_id: str | None = None
    requested_ttl_seconds: float | None = None
    selected_heuristic_id: str | None = None
    selected_version: str | None = None
    proposed_by: str = "ananta-worker"  # never "opencode"
    reason: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "context_hash": self.context_hash,
            "available_heuristic_ids": self.available_heuristic_ids,
            "current_lease_id": self.current_lease_id,
            "requested_ttl_seconds": self.requested_ttl_seconds,
            "selected_heuristic_id": self.selected_heuristic_id,
            "selected_version": self.selected_version,
            "proposed_by": self.proposed_by,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass
class HeuristicSelectionResponse:
    heuristic_id: str
    version: str
    confidence: float
    reason: str
    requested_ttl_seconds: float
    lease_id: str | None = None
    accepted: bool = True
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "heuristic_id": self.heuristic_id,
            "version": self.version,
            "confidence": self.confidence,
            "reason": self.reason,
            "requested_ttl_seconds": self.requested_ttl_seconds,
            "lease_id": self.lease_id,
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
        }


class HeuristicSelectionError(ValueError):
    pass


_BLOCKED_CONTROL_WORKERS = frozenset({"opencode", "opencode-worker"})


class HeuristicSelectionService:
    def __init__(
        self,
        registry: HeuristicRegistry | None = None,
        lease_repo: HeuristicLeaseRepository | None = None,
        config_service: WorkerRoleConfigService | None = None,
    ) -> None:
        self._registry = registry or get_heuristic_registry()
        self._lease_repo = lease_repo or HeuristicLeaseRepository()
        self._config_service = config_service or WorkerRoleConfigService()

    def select(self, request: HeuristicSelectionRequest) -> HeuristicSelectionResponse:
        """Validate request and acquire a lease for the selected heuristic."""
        # Block opencode as control worker
        if request.proposed_by.lower() in _BLOCKED_CONTROL_WORKERS:
            return HeuristicSelectionResponse(
                heuristic_id="",
                version="",
                confidence=0.0,
                reason="",
                requested_ttl_seconds=0.0,
                accepted=False,
                rejection_reason="opencode_not_allowed_as_control_worker",
            )

        # Resolve heuristic_id (explicitly selected or best from available)
        heuristic_id = request.selected_heuristic_id
        if not heuristic_id and request.available_heuristic_ids:
            heuristic_id = request.available_heuristic_ids[0]
        if not heuristic_id:
            return HeuristicSelectionResponse(
                heuristic_id="", version="", confidence=0.0, reason="",
                requested_ttl_seconds=0.0, accepted=False,
                rejection_reason="no_heuristic_id_provided",
            )

        # Validate heuristic exists in registry as active
        try:
            hdef = self._registry.get_by_id(heuristic_id)
        except Exception:
            return HeuristicSelectionResponse(
                heuristic_id=heuristic_id, version="", confidence=0.0, reason="",
                requested_ttl_seconds=0.0, accepted=False,
                rejection_reason="heuristic_not_found_in_registry",
            )
        if hdef.status != "active":
            return HeuristicSelectionResponse(
                heuristic_id=heuristic_id, version=hdef.version, confidence=0.0, reason="",
                requested_ttl_seconds=0.0, accepted=False,
                rejection_reason=f"heuristic_status_not_active:{hdef.status}",
            )

        # Validate TTL against domain policy
        domain_defaults = _DOMAIN_TTL_DEFAULTS.get(request.domain, {"min": 1.0, "max": 60.0, "default": 7.0})
        ttl = request.requested_ttl_seconds or domain_defaults["default"]
        if ttl < domain_defaults["min"] or ttl > domain_defaults["max"]:
            return HeuristicSelectionResponse(
                heuristic_id=heuristic_id, version=hdef.version, confidence=0.0, reason="",
                requested_ttl_seconds=ttl, accepted=False,
                rejection_reason=f"ttl_out_of_range:{ttl:.1f}s (allowed {domain_defaults['min']}–{domain_defaults['max']})",
            )

        # Acquire lease
        lease = self._lease_repo.acquire(
            heuristic_id=heuristic_id,
            version=request.selected_version or hdef.version,
            domain=request.domain,
            context_hash=request.context_hash,
            selected_by="ai",
            ttl_seconds=ttl,
            reason_codes=["ai_selected", f"proposed_by:{request.proposed_by}"],
        )

        return HeuristicSelectionResponse(
            heuristic_id=heuristic_id,
            version=hdef.version,
            confidence=request.confidence,
            reason=request.reason,
            requested_ttl_seconds=ttl,
            lease_id=lease.id,
            accepted=True,
        )
