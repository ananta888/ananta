"""CAS ledger for reservations, consumption and publication fences."""

from __future__ import annotations

from dataclasses import replace

from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_reconciliation_budget_repository_port import (
    SpeechReconciliationBudgetRepositoryPort,
)
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationContractError,
    SpeechResourceVector,
)


class SpeechReconciliationBudgetLedgerService:
    def __init__(
        self,
        repository: SpeechReconciliationBudgetRepositoryPort,
        *,
        tenant_id: str | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id
        self._audit = audit

    def create(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        stage: str,
        source_duration_ms: int,
        compute_factor: int,
        allocated: SpeechResourceVector,
    ) -> SpeechReconciliationBudgetLedger:
        ledger = SpeechReconciliationBudgetLedger.from_mapping(
            {
                "contract_version": CONTRACT_VERSION,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "fencing_epoch": fencing_epoch,
                "sequence": 0,
                "stage": stage,
                "source_duration_ms": source_duration_ms,
                "compute_factor": compute_factor,
                "allocated": allocated.to_dict(),
                "reserved": SpeechResourceVector().to_dict(),
                "consumed": SpeechResourceVector().to_dict(),
                "remaining": allocated.to_dict(),
            }
        )
        event = self._prepare_audit(ledger, "created", "speech_budget_ledger_created")
        if not self._repository.compare_and_swap(
            expected_sequence=None,
            ledger=ledger,
            audit_event=event,
        ):
            current = self._repository.get(job_id=job_id)
            if current == ledger:
                return self._after_audit(current, event)
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_conflict")
        return self._after_audit(ledger, event)

    def reserve(
        self,
        *,
        job_id: str,
        expected_sequence: int,
        fencing_epoch: int,
        amount: SpeechResourceVector,
        stage: str,
    ) -> SpeechReconciliationBudgetLedger:
        current = self._current(job_id, expected_sequence, fencing_epoch)
        reserved = current.reserved.add(amount)
        remaining = current.allocated.subtract(current.consumed.add(reserved))
        return self._cas(
            current,
            transition="reserved",
            reason_code="speech_budget_reserved",
            reserved=reserved,
            remaining=remaining,
            stage=stage,
        )

    def consume(
        self,
        *,
        job_id: str,
        expected_sequence: int,
        fencing_epoch: int,
        amount: SpeechResourceVector,
        stage: str,
    ) -> SpeechReconciliationBudgetLedger:
        current = self._current(job_id, expected_sequence, fencing_epoch)
        if not current.reserved.covers(amount):
            raise SpeechReconciliationContractError("speech_reconciliation_unreserved_consumption")
        reserved = current.reserved.subtract(amount)
        consumed = current.consumed.add(amount)
        remaining = current.allocated.subtract(consumed.add(reserved))
        return self._cas(
            current,
            transition="consumed",
            reason_code="speech_budget_consumed",
            reserved=reserved,
            consumed=consumed,
            remaining=remaining,
            stage=stage,
        )

    def release(
        self,
        *,
        job_id: str,
        expected_sequence: int,
        fencing_epoch: int,
        amount: SpeechResourceVector,
        stage: str,
    ) -> SpeechReconciliationBudgetLedger:
        current = self._current(job_id, expected_sequence, fencing_epoch)
        reserved = current.reserved.subtract(amount)
        remaining = current.allocated.subtract(current.consumed.add(reserved))
        return self._cas(
            current,
            transition="released",
            reason_code="speech_budget_released",
            reserved=reserved,
            remaining=remaining,
            stage=stage,
        )

    def authorize_publication(self, *, job_id: str, sequence: int, fencing_epoch: int) -> bool:
        current = self._repository.get(job_id=job_id)
        return bool(
            current is not None
            and current.sequence == sequence
            and current.fencing_epoch == fencing_epoch
            and current.reserved == SpeechResourceVector()
        )

    def rebind_attempt(
        self,
        *,
        job_id: str,
        expected_sequence: int,
        attempt_id: str,
        fencing_epoch: int,
        stage: str,
    ) -> SpeechReconciliationBudgetLedger:
        """Fence stale reservations while preserving immutable consumption."""

        current = self._repository.get(job_id=job_id)
        if current is None or current.sequence != expected_sequence:
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_stale")
        if fencing_epoch <= current.fencing_epoch:
            raise SpeechReconciliationContractError("speech_reconciliation_fence_stale")
        remaining = current.allocated.subtract(current.consumed)
        updated = replace(
            current,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            sequence=current.sequence + 1,
            stage=stage,
            reserved=SpeechResourceVector(),
            remaining=remaining,
        )
        updated = SpeechReconciliationBudgetLedger.from_mapping(updated.to_dict())
        event = self._prepare_audit(updated, "rebound", "speech_budget_attempt_rebound")
        if not self._repository.compare_and_swap(
            expected_sequence=current.sequence,
            ledger=updated,
            audit_event=event,
        ):
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_stale")
        return self._after_audit(updated, event)

    def _current(self, job_id: str, sequence: int, fencing_epoch: int) -> SpeechReconciliationBudgetLedger:
        current = self._repository.get(job_id=job_id)
        if current is None:
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_not_found")
        if current.sequence != sequence:
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_stale")
        if current.fencing_epoch != fencing_epoch:
            raise SpeechReconciliationContractError("speech_reconciliation_fence_stale")
        return current

    def _cas(
        self,
        current: SpeechReconciliationBudgetLedger,
        *,
        transition: str,
        reason_code: str,
        **changes,
    ) -> SpeechReconciliationBudgetLedger:
        updated = replace(current, sequence=current.sequence + 1, **changes)
        # Round-trip through the shared parser so internal arithmetic can never
        # bypass the same validation applied to worker payloads.
        updated = SpeechReconciliationBudgetLedger.from_mapping(updated.to_dict())
        event = self._prepare_audit(updated, transition, reason_code)
        if not self._repository.compare_and_swap(
            expected_sequence=current.sequence,
            ledger=updated,
            audit_event=event,
        ):
            raise SpeechReconciliationContractError("speech_reconciliation_ledger_stale")
        return self._after_audit(updated, event)

    def _prepare_audit(
        self,
        ledger: SpeechReconciliationBudgetLedger,
        transition: str,
        reason_code: str,
    ):
        if self._audit is None:
            return None
        if not self._tenant_id:
            raise SpeechReconciliationContractError("semantic_audit_tenant_missing")
        try:
            return self._audit.prepare_transition(
                idempotency_key=f"speech-budget:{ledger.job_id}:{ledger.sequence}:{transition}",
                tenant_id=self._tenant_id,
                scope=f"speech-job:{ledger.job_id}",
                event_type="semantic_budget",
                transition=transition,
                reason_code=reason_code,
                epoch=ledger.fencing_epoch,
                lease_ref=ledger.attempt_id,
                job_ref=ledger.job_id,
            )
        except Exception as exc:
            raise SpeechReconciliationContractError("semantic_audit_unavailable") from exc

    def _after_audit(self, ledger, event):
        if event is None or bool(getattr(self._repository, "transactional_audit", False)):
            return ledger
        try:
            self._audit.append_prepared(event)
        except Exception as exc:
            raise SpeechReconciliationContractError("semantic_audit_unavailable") from exc
        return ledger


__all__ = ["SpeechReconciliationBudgetLedgerService"]
