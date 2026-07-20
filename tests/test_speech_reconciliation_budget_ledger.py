from __future__ import annotations

import pytest

from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_reconciliation_budget_ledger_service import SpeechReconciliationBudgetLedgerService
from agent.services.speech_reconciliation_budget_repository_port import InMemorySpeechReconciliationBudgetRepository
from agent.services.speech_reconciliation_budget_service import (
    AdmittedSourceDuration,
    SpeechReconciliationBudgetService,
)
from ananta_contracts.speech_reconciliation import SpeechReconciliationContractError, SpeechResourceVector
from tests.speech_reconciliation_support import digest


@pytest.mark.parametrize(("factor", "maximum_minutes"), [(10, 100), (20, 200)])
def test_ten_minutes_are_deduplicated_and_factor_bounded(factor: int, maximum_minutes: int) -> None:
    plan = SpeechReconciliationBudgetService().plan(
        [
            AdmittedSourceDuration(digest("source-a"), 10 * 60_000),
            AdmittedSourceDuration(digest("source-a"), 10 * 60_000),
        ],
        compute_factor=factor,
    )
    assert plan.source_duration_ms == 10 * 60_000
    assert plan.compute_equivalent_ms <= maximum_minutes * 60_000
    assert set(plan.stages) == {"staging", "slow_asr", "alignment", "resolution", "dataset", "evaluation"}


def test_reservation_consumption_use_monotone_cas_and_fence_publication() -> None:
    repository = InMemorySpeechReconciliationBudgetRepository()
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"speech-budget-audit-test-key" * 2,
    )
    service = SpeechReconciliationBudgetLedgerService(
        repository,
        tenant_id="tenant-ledger",
        audit=audit,
    )
    allocated = SpeechResourceVector(wall_time_ms=100, cpu_time_ms=100, disk_bytes=100)
    initial = service.create(
        job_id="job-ledger",
        attempt_id="attempt-ledger",
        fencing_epoch=3,
        stage="slow_asr",
        source_duration_ms=1000,
        compute_factor=1,
        allocated=allocated,
    )
    amount = SpeechResourceVector(wall_time_ms=20, cpu_time_ms=10, disk_bytes=5)
    reserved = service.reserve(
        job_id=initial.job_id,
        expected_sequence=0,
        fencing_epoch=3,
        amount=amount,
        stage="slow_asr",
    )
    assert not service.authorize_publication(job_id=initial.job_id, sequence=reserved.sequence, fencing_epoch=3)
    consumed = service.consume(
        job_id=initial.job_id,
        expected_sequence=1,
        fencing_epoch=3,
        amount=amount,
        stage="slow_asr",
    )
    assert consumed.sequence == 2
    assert service.authorize_publication(job_id=initial.job_id, sequence=2, fencing_epoch=3)
    rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", "tenant-ledger"),
        scope_digest=audit.digest("scope", "speech-job:job-ledger"),
        after_event_id=None,
        limit=10,
        now_ms=1_000_000,
    )
    assert [row.transition for row in rows] == ["created", "reserved", "consumed"]
    with pytest.raises(SpeechReconciliationContractError, match="speech_reconciliation_ledger_stale"):
        service.reserve(
            job_id=initial.job_id,
            expected_sequence=1,
            fencing_epoch=3,
            amount=amount,
            stage="slow_asr",
        )
    with pytest.raises(SpeechReconciliationContractError, match="speech_reconciliation_budget_exceeded"):
        service.reserve(
            job_id=initial.job_id,
            expected_sequence=2,
            fencing_epoch=3,
            amount=SpeechResourceVector(wall_time_ms=1000),
            stage="slow_asr",
        )
