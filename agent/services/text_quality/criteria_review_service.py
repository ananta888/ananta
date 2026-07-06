from __future__ import annotations

from .criteria_service import CriteriaService, get_criteria_service


class CriteriaReviewService:
    """Review boundary for criteria lifecycle changes."""

    def __init__(self, criteria_service: CriteriaService | None = None) -> None:
        self.criteria_service = criteria_service or get_criteria_service()

    def activate(self, criteria_id: str, *, actor: str, source: str):
        self._require_provenance(actor, source)
        return self.criteria_service.set_status(criteria_id, "enabled")

    def reject(self, criteria_id: str, *, actor: str, source: str):
        self._require_provenance(actor, source)
        return self.criteria_service.set_status(criteria_id, "rejected")

    def archive(self, criteria_id: str, *, actor: str, source: str):
        self._require_provenance(actor, source)
        return self.criteria_service.set_status(criteria_id, "archived")

    @staticmethod
    def _require_provenance(actor: str, source: str) -> None:
        if not actor.strip() or not source.strip():
            raise ValueError("review_provenance_required")
