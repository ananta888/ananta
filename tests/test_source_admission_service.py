from __future__ import annotations

import pytest

from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceAdmissionError,
    SourceAdmissionState,
    SourceInventoryEvidence,
    SourceScanEvidence,
    evaluate_source_admission,
)
from ananta_contracts.source_control import MAX_SOURCE_ADMISSION_FILES


def _inventory(**overrides) -> SourceInventoryEvidence:
    values = {
        "revision_digest": "a" * 64,
        "manifest_digest": "b" * 64,
        "file_count": 2,
        "total_bytes": 200,
        "largest_file_bytes": 120,
        "archive_expansion_ratio": 1.0,
        "file_type_counts": {"python": 2},
    }
    values.update(overrides)
    return SourceInventoryEvidence(**values)


def _scan(**overrides) -> SourceScanEvidence:
    values = {
        "revision_digest": "a" * 64,
        "manifest_digest": "b" * 64,
        "scanner_id": "scanner-example",
        "scanner_version": "1.0",
        "completed": True,
    }
    values.update(overrides)
    return SourceScanEvidence(**values)


def _evaluate(*, inventory=None, scan=None, budgets=None):
    return evaluate_source_admission(
        tenant_id="tenant-example",
        project_id="project-example",
        source_revision_id="revision-example",
        revision_digest="a" * 64,
        policy_digest="c" * 64,
        inventory=inventory or _inventory(),
        scan=scan or _scan(),
        budgets=budgets
        or SourceAdmissionBudgets(allowed_file_types=frozenset({"python"})),
    )


def test_matching_clean_evidence_is_admitted_by_hub() -> None:
    decision = _evaluate()

    assert decision.authority == "hub"
    assert decision.state is SourceAdmissionState.admitted
    assert decision.reason_codes == ()
    assert len(decision.admission_digest) == 64


def test_source_file_admission_boundary_is_shared_and_fail_closed() -> None:
    budgets = SourceAdmissionBudgets(
        allowed_file_types=frozenset({"python"})
    )
    exact = _inventory(
        file_count=MAX_SOURCE_ADMISSION_FILES,
        total_bytes=MAX_SOURCE_ADMISSION_FILES,
        largest_file_bytes=1,
        file_type_counts={
            "python": MAX_SOURCE_ADMISSION_FILES
        },
    )
    overflow = _inventory(
        file_count=MAX_SOURCE_ADMISSION_FILES + 1,
        total_bytes=MAX_SOURCE_ADMISSION_FILES + 1,
        largest_file_bytes=1,
        file_type_counts={
            "python": MAX_SOURCE_ADMISSION_FILES + 1
        },
    )

    assert _evaluate(inventory=exact, budgets=budgets).state is (
        SourceAdmissionState.admitted
    )
    rejected = _evaluate(inventory=overflow, budgets=budgets)
    assert rejected.state is SourceAdmissionState.blocked
    assert "file_count_budget_exceeded" in rejected.reason_codes
    with pytest.raises(SourceAdmissionError, match="admission_budgets_invalid"):
        SourceAdmissionBudgets(
            max_files=MAX_SOURCE_ADMISSION_FILES + 1
        )


@pytest.mark.parametrize(
    ("inventory", "scan", "reason_code"),
    (
        (_inventory(symlink_count=1), _scan(), "symlink_forbidden"),
        (_inventory(hardlink_count=1), _scan(), "hardlink_forbidden"),
        (_inventory(sparse_file_count=1), _scan(), "sparse_file_forbidden"),
        (_inventory(binary_count=1), _scan(), "binary_forbidden"),
        (_inventory(archive_count=1), _scan(), "archive_forbidden"),
        (_inventory(), _scan(secret_findings=1), "secret_detected"),
        (
            _inventory(),
            _scan(injection_findings=1),
            "prompt_injection_detected",
        ),
        (_inventory(), _scan(completed=False), "scan_incomplete"),
    ),
)
def test_security_findings_are_fail_closed(
    inventory: SourceInventoryEvidence,
    scan: SourceScanEvidence,
    reason_code: str,
) -> None:
    decision = _evaluate(inventory=inventory, scan=scan)

    assert decision.state is SourceAdmissionState.blocked
    assert reason_code in decision.reason_codes


def test_revision_or_manifest_mismatch_is_rejected() -> None:
    with pytest.raises(SourceAdmissionError, match="scan_revision_mismatch"):
        _evaluate(scan=_scan(revision_digest="d" * 64))
    with pytest.raises(SourceAdmissionError, match="scan_manifest_mismatch"):
        _evaluate(scan=_scan(manifest_digest="d" * 64))


def test_admission_is_deterministic_and_content_free() -> None:
    first = _evaluate()
    second = _evaluate()

    assert first == second
    assert not hasattr(first, "content")
    assert not hasattr(first, "path")
