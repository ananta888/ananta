"""Mandatory Hub gate for releasing a connector revision to indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceAdmissionDecision,
    SourceAdmissionError,
    SourceAdmissionState,
    SourceInventoryEvidence,
    SourceScanEvidence,
    evaluate_source_admission,
)


class SourceIndexAdmissionError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        decision: SourceAdmissionDecision | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.decision = decision


@dataclass(frozen=True)
class SourceIndexAdmissionRequest:
    tenant_id: str
    project_id: str
    source_revision_id: str
    revision_digest: str
    policy_digest: str
    inventory: SourceInventoryEvidence
    scan: SourceScanEvidence


class SourceAdmissionGatePort(Protocol):
    def require_admitted(
        self,
        request: SourceIndexAdmissionRequest,
    ) -> SourceAdmissionDecision: ...


class EvaluatingSourceAdmissionGate:
    """Evaluate immutable evidence and fail closed on every blocked decision."""

    def __init__(self, *, budgets: SourceAdmissionBudgets) -> None:
        self._budgets = budgets

    def require_admitted(
        self,
        request: SourceIndexAdmissionRequest,
    ) -> SourceAdmissionDecision:
        try:
            decision = evaluate_source_admission(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                source_revision_id=request.source_revision_id,
                revision_digest=request.revision_digest,
                policy_digest=request.policy_digest,
                inventory=request.inventory,
                scan=request.scan,
                budgets=self._budgets,
            )
        except SourceAdmissionError as exc:
            raise SourceIndexAdmissionError(str(exc)) from None
        if decision.state is not SourceAdmissionState.admitted:
            raise SourceIndexAdmissionError(
                "source_admission_blocked",
                decision=decision,
            )
        return decision


__all__ = [
    "EvaluatingSourceAdmissionGate",
    "SourceAdmissionGatePort",
    "SourceIndexAdmissionError",
    "SourceIndexAdmissionRequest",
]
