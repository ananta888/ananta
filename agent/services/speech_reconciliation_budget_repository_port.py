"""Persistence seam for monotone reconciliation budget ledgers."""

from __future__ import annotations

import threading
from typing import Protocol

from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from ananta_contracts.speech_reconciliation import SpeechReconciliationBudgetLedger


class SpeechReconciliationBudgetRepositoryPort(Protocol):
    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None: ...

    def compare_and_swap(
        self,
        *,
        expected_sequence: int | None,
        ledger: SpeechReconciliationBudgetLedger,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool: ...


class InMemorySpeechReconciliationBudgetRepository:
    def __init__(self) -> None:
        self._items: dict[str, SpeechReconciliationBudgetLedger] = {}
        self._lock = threading.RLock()

    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None:
        with self._lock:
            return self._items.get(job_id)

    def compare_and_swap(
        self,
        *,
        expected_sequence: int | None,
        ledger: SpeechReconciliationBudgetLedger,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        del audit_event
        with self._lock:
            current = self._items.get(ledger.job_id)
            actual = current.sequence if current is not None else None
            if actual != expected_sequence:
                return False
            self._items[ledger.job_id] = ledger
            return True


__all__ = [
    "InMemorySpeechReconciliationBudgetRepository",
    "SpeechReconciliationBudgetRepositoryPort",
]
