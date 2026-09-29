from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from agent.services.business_controlling_reconciliation import (
    BusinessControllingReconciliationService,
    ReconciliationCheck,
    ReconciliationCheckKind,
    ReconciliationPolicy,
    ReconciliationRecord,
    ReconciliationStatus,
)
from ananta_contracts.business_controlling import CONTRACT_VERSION, DatasetReceipt, FindingKind, RecordLocator


def _dataset() -> DatasetReceipt:
    return DatasetReceipt.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "dataset_id": "dataset-a",
            "dataset_version": "version-a",
            "source_digest": "a" * 64,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "currency": "EUR",
            "column_mapping": {"amount": "Amount"},
        }
    )


def _policy(**changes: object) -> ReconciliationPolicy:
    values = {
        "version": "v1",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
        "fiscal_period": "FY2026-P01",
        "rounding_policy": "half_even",
        "decimal_places": 2,
        "absolute_tolerance": Decimal("0.01"),
        "relative_tolerance": Decimal("0"),
        "aggregation_grain": ("cost_center",),
        "allowed_exclusion_reasons": ("cancelled",),
    }
    values.update(changes)
    return ReconciliationPolicy(**values)  # type: ignore[arg-type]


def _record(
    row: int,
    amount: str | None,
    *,
    dimensions: tuple[tuple[str, str], ...] = (("cost_center", "CC-1"),),
    included: bool = True,
    exclusion_reason: str | None = None,
    currency: str = "EUR",
) -> ReconciliationRecord:
    return ReconciliationRecord(
        locator=RecordLocator.from_mapping(
            {
                "source_kind": "csv",
                "source_version": "version-a",
                "locator_kind": "csv_row",
                "locator": f"row_{row}",
            }
        ),
        amount=None if amount is None else Decimal(amount),
        currency=currency,
        timezone="Europe/Berlin",
        fiscal_period="FY2026-P01",
        dimensions=dimensions,
        included=included,
        exclusion_reason=exclusion_reason,
    )


def _check(
    *,
    actual: str,
    expected: str = "100.00",
    kind: ReconciliationCheckKind = ReconciliationCheckKind.SOURCE_TARGET,
    policy: ReconciliationPolicy | None = None,
) -> ReconciliationCheck:
    return ReconciliationCheck(
        check_id=f"check_{kind.value}",
        kind=kind,
        policy=policy or _policy(),
        expected_records=(_record(2, expected),),
        actual_records=(_record(3, actual),),
    )


def test_valid_and_tolerance_boundary_reports_explicit_inputs_and_counts() -> None:
    result = BusinessControllingReconciliationService().evaluate(
        dataset=_dataset(),
        checks=(_check(actual="100.01"),),
    )
    report = result.reports[0]
    assert report.status is ReconciliationStatus.PASS
    assert (report.expected, report.actual, report.difference, report.tolerance) == (
        Decimal("100.00"),
        Decimal("100.01"),
        Decimal("0.01"),
        Decimal("0.01"),
    )
    assert (report.expected_included_count, report.actual_included_count) == (1, 1)
    assert (report.expected_excluded_count, report.actual_excluded_count) == (0, 0)
    assert report.aggregation_grain == ("cost_center",)
    assert report.grain_values == (("cost_center", "CC-1"),)
    assert (report.currency, report.timezone, report.fiscal_period, report.rounding_policy) == (
        "EUR",
        "Europe/Berlin",
        "FY2026-P01",
        "half_even",
    )
    assert result.findings == ()


def test_mismatch_emits_reconciliation_finding_and_is_deterministic() -> None:
    service = BusinessControllingReconciliationService()
    check = _check(actual="100.02")
    first = service.evaluate(dataset=_dataset(), checks=(check,))
    second = service.evaluate(dataset=_dataset(), checks=(check,))
    assert first == second
    assert first.reports[0].status is ReconciliationStatus.MISMATCH
    assert first.reports[0].difference == Decimal("0.02")
    assert len(first.findings) == 1
    assert first.findings[0].kind is FindingKind.RECONCILIATION_MISMATCH
    assert first.findings[0].rule_id == "reconcile_source_target"


@pytest.mark.parametrize(
    ("kind", "expected_label", "actual_label"),
    [
        (ReconciliationCheckKind.DEBIT_CREDIT, "debit", "credit"),
        (ReconciliationCheckKind.SOURCE_TARGET, "source", "target"),
        (ReconciliationCheckKind.LINE_HEADER, "line_total", "header_total"),
        (ReconciliationCheckKind.SUBTOTAL_TAX_TOTAL, "subtotal_plus_tax", "total"),
        (ReconciliationCheckKind.PERIOD_OVER_PERIOD, "prior_period", "current_period"),
        (ReconciliationCheckKind.BUDGET_ACTUAL, "budget", "actual"),
    ],
)
def test_all_declared_reconciliation_and_variance_kinds_are_supported(
    kind: ReconciliationCheckKind,
    expected_label: str,
    actual_label: str,
) -> None:
    policy = _policy(relative_tolerance=Decimal("0.05"), absolute_tolerance=Decimal("0"))
    report = BusinessControllingReconciliationService().evaluate(
        dataset=_dataset(),
        checks=(_check(kind=kind, expected="100.00", actual="105.00", policy=policy),),
    ).reports[0]
    assert report.status is ReconciliationStatus.PASS
    assert (report.expected_label, report.actual_label, report.tolerance) == (
        expected_label,
        actual_label,
        Decimal("5.00"),
    )


@pytest.mark.parametrize(
    "kind",
    [ReconciliationCheckKind.LINE_HEADER, ReconciliationCheckKind.SUBTOTAL_TAX_TOTAL],
)
def test_line_header_and_subtotal_tax_relationships_sum_the_component_side(
    kind: ReconciliationCheckKind,
) -> None:
    check = ReconciliationCheck(
        check_id=f"components_{kind.value}",
        kind=kind,
        policy=_policy(),
        expected_records=(_record(2, "80.00"), _record(3, "20.00")),
        actual_records=(_record(4, "100.00"),),
    )
    report = BusinessControllingReconciliationService().evaluate(dataset=_dataset(), checks=(check,)).reports[0]
    assert report.status is ReconciliationStatus.PASS
    assert (report.expected, report.actual, report.expected_included_count) == (
        Decimal("100.00"),
        Decimal("100.00"),
        2,
    )


@pytest.mark.parametrize(
    "actual",
    [
        (_record(3, None),),
        (_record(3, "100.00", currency="USD"),),
        (_record(3, "100.00", dimensions=(("account", "A1"),)),),
        (_record(3, "100.00", dimensions=(("cost_center", "CC-1"), ("account", "A1"))),),
    ],
)
def test_incomplete_metadata_or_mixed_grain_is_inconclusive_not_pass(
    actual: tuple[ReconciliationRecord, ...],
) -> None:
    check = replace(_check(actual="100.00"), actual_records=actual)
    evaluation = BusinessControllingReconciliationService().evaluate(dataset=_dataset(), checks=(check,))
    assert all(report.status is ReconciliationStatus.INCONCLUSIVE for report in evaluation.reports)
    assert evaluation.findings == ()
    assert evaluation.reports[0].actual_excluded_count == 1


def test_declared_exclusions_are_counted_without_hiding_complete_comparison() -> None:
    check = replace(
        _check(actual="100.00"),
        expected_records=(
            _record(2, "100.00"),
            _record(4, "999.00", included=False, exclusion_reason="cancelled"),
        ),
    )
    report = BusinessControllingReconciliationService().evaluate(dataset=_dataset(), checks=(check,)).reports[0]
    assert report.status is ReconciliationStatus.PASS
    assert report.expected_excluded_count == 1
    assert report.exclusion_reasons == (("cancelled", 1),)


def test_rounding_policy_is_applied_before_tolerance_decision() -> None:
    report = BusinessControllingReconciliationService().evaluate(
        dataset=_dataset(),
        checks=(
            _check(
                expected="10.005",
                actual="10.00",
                policy=_policy(absolute_tolerance=Decimal("0")),
            ),
        ),
    ).reports[0]
    assert report.expected == Decimal("10.00")
    assert report.status is ReconciliationStatus.PASS
