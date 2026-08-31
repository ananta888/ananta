"""Deterministic, grain-safe reconciliation and variance comparisons."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ananta_contracts.business_controlling import (
    CONTRACT_VERSION,
    BusinessFinding,
    DatasetReceipt,
    ExecutionReceipt,
    FindingDisposition,
    FindingKind,
    FindingSeverity,
    RecordLocator,
    derive_stable_id,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROUNDING = {
    "half_even": ROUND_HALF_EVEN,
    "half_up": ROUND_HALF_UP,
    "down": ROUND_DOWN,
}


class BusinessControllingReconciliationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ReconciliationCheckKind(str, Enum):
    DEBIT_CREDIT = "debit_credit"
    SOURCE_TARGET = "source_target"
    LINE_HEADER = "line_header"
    SUBTOTAL_TAX_TOTAL = "subtotal_tax_total"
    PERIOD_OVER_PERIOD = "period_over_period"
    BUDGET_ACTUAL = "budget_actual"


class ReconciliationStatus(str, Enum):
    PASS = "pass"
    MISMATCH = "mismatch"
    INCONCLUSIVE = "inconclusive"


_SIDE_LABELS = {
    ReconciliationCheckKind.DEBIT_CREDIT: ("debit", "credit"),
    ReconciliationCheckKind.SOURCE_TARGET: ("source", "target"),
    ReconciliationCheckKind.LINE_HEADER: ("line_total", "header_total"),
    ReconciliationCheckKind.SUBTOTAL_TAX_TOTAL: ("subtotal_plus_tax", "total"),
    ReconciliationCheckKind.PERIOD_OVER_PERIOD: ("prior_period", "current_period"),
    ReconciliationCheckKind.BUDGET_ACTUAL: ("budget", "actual"),
}


@dataclass(frozen=True)
class ReconciliationPolicy:
    version: str
    currency: str
    timezone: str
    fiscal_period: str
    rounding_policy: str
    decimal_places: int
    absolute_tolerance: Decimal
    relative_tolerance: Decimal
    aggregation_grain: tuple[str, ...]
    allowed_exclusion_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifiers = (
            self.version,
            self.fiscal_period,
            *self.aggregation_grain,
            *self.allowed_exclusion_reasons,
        )
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in identifiers):
            raise BusinessControllingReconciliationError("controlling_reconciliation_policy_identifier_invalid")
        if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise BusinessControllingReconciliationError("controlling_reconciliation_currency_invalid")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise BusinessControllingReconciliationError("controlling_reconciliation_timezone_invalid") from exc
        if self.rounding_policy not in _ROUNDING or not 0 <= self.decimal_places <= 6:
            raise BusinessControllingReconciliationError("controlling_reconciliation_rounding_invalid")
        if (
            not isinstance(self.absolute_tolerance, Decimal)
            or not isinstance(self.relative_tolerance, Decimal)
            or not self.absolute_tolerance.is_finite()
            or self.absolute_tolerance < 0
            or not self.relative_tolerance.is_finite()
            or not Decimal("0") <= self.relative_tolerance <= Decimal("1")
        ):
            raise BusinessControllingReconciliationError("controlling_reconciliation_tolerance_invalid")
        if (
            not self.aggregation_grain
            or len(set(self.aggregation_grain)) != len(self.aggregation_grain)
            or len(set(self.allowed_exclusion_reasons)) != len(self.allowed_exclusion_reasons)
        ):
            raise BusinessControllingReconciliationError("controlling_reconciliation_grain_invalid")

    def to_projection(self) -> dict[str, object]:
        return {
            "version": self.version,
            "currency": self.currency,
            "timezone": self.timezone,
            "fiscal_period": self.fiscal_period,
            "rounding_policy": self.rounding_policy,
            "decimal_places": self.decimal_places,
            "absolute_tolerance": str(self.absolute_tolerance),
            "relative_tolerance": str(self.relative_tolerance),
            "aggregation_grain": list(self.aggregation_grain),
            "allowed_exclusion_reasons": list(self.allowed_exclusion_reasons),
        }


@dataclass(frozen=True)
class ReconciliationRecord:
    locator: RecordLocator
    amount: Decimal | None
    currency: str
    timezone: str
    fiscal_period: str
    dimensions: tuple[tuple[str, str], ...]
    included: bool = True
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class ReconciliationCheck:
    check_id: str
    kind: ReconciliationCheckKind
    policy: ReconciliationPolicy
    expected_records: tuple[ReconciliationRecord, ...]
    actual_records: tuple[ReconciliationRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.check_id, str) or not _IDENTIFIER.fullmatch(self.check_id):
            raise BusinessControllingReconciliationError("controlling_reconciliation_check_id_invalid")
        if not isinstance(self.kind, ReconciliationCheckKind):
            raise BusinessControllingReconciliationError("controlling_reconciliation_check_kind_invalid")


@dataclass(frozen=True)
class ReconciliationReport:
    report_id: str
    check_id: str
    check_kind: ReconciliationCheckKind
    status: ReconciliationStatus
    expected_label: str
    actual_label: str
    aggregation_grain: tuple[str, ...]
    grain_values: tuple[tuple[str, str], ...]
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    tolerance: Decimal
    expected_included_count: int
    actual_included_count: int
    expected_excluded_count: int
    actual_excluded_count: int
    exclusion_reasons: tuple[tuple[str, int], ...]
    currency: str
    timezone: str
    fiscal_period: str
    rounding_policy: str
    policy_version: str

    def to_projection(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "check_id": self.check_id,
            "check_kind": self.check_kind.value,
            "status": self.status.value,
            "expected_label": self.expected_label,
            "actual_label": self.actual_label,
            "aggregation_grain": list(self.aggregation_grain),
            "grain_values": dict(self.grain_values),
            "expected": None if self.expected is None else str(self.expected),
            "actual": None if self.actual is None else str(self.actual),
            "difference": None if self.difference is None else str(self.difference),
            "tolerance": str(self.tolerance),
            "expected_included_count": self.expected_included_count,
            "actual_included_count": self.actual_included_count,
            "expected_excluded_count": self.expected_excluded_count,
            "actual_excluded_count": self.actual_excluded_count,
            "exclusion_reasons": dict(self.exclusion_reasons),
            "currency": self.currency,
            "timezone": self.timezone,
            "fiscal_period": self.fiscal_period,
            "rounding_policy": self.rounding_policy,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ReconciliationEvaluation:
    execution_receipt: ExecutionReceipt
    reports: tuple[ReconciliationReport, ...]
    findings: tuple[BusinessFinding, ...]


@dataclass(frozen=True)
class _PreparedSide:
    groups: Mapping[tuple[tuple[str, str], ...], tuple[ReconciliationRecord, ...]]
    excluded_count: int
    exclusion_reasons: Counter[str]
    incomplete: bool


class BusinessControllingReconciliationService:
    """Compares declared sides per exact grain; incomplete inputs never pass."""

    MAX_CHECKS = 100
    MAX_RECORDS_PER_CHECK = 200_000

    def evaluate(
        self,
        *,
        dataset: DatasetReceipt,
        checks: Sequence[ReconciliationCheck],
    ) -> ReconciliationEvaluation:
        if not checks or len(checks) > self.MAX_CHECKS or len({check.check_id for check in checks}) != len(checks):
            raise BusinessControllingReconciliationError("controlling_reconciliation_checks_invalid")
        reports: list[ReconciliationReport] = []
        locator_by_report: dict[str, RecordLocator] = {}
        for check in checks:
            if len(check.expected_records) + len(check.actual_records) > self.MAX_RECORDS_PER_CHECK:
                raise BusinessControllingReconciliationError("controlling_reconciliation_record_budget_exceeded")
            if check.policy.currency != dataset.currency:
                raise BusinessControllingReconciliationError("controlling_reconciliation_dataset_currency_mismatch")
            check_reports, check_locators = self._evaluate_check(check, dataset=dataset)
            reports.extend(check_reports)
            locator_by_report.update(check_locators)

        configuration_digest = _digest(
            [
                {"check_id": check.check_id, "kind": check.kind.value, "policy": check.policy.to_projection()}
                for check in checks
            ]
        )
        input_digest = _digest(
            {
                "dataset": dataset.to_dict(),
                "checks": [
                    {
                        "check_id": check.check_id,
                        "expected": [_record_projection(record) for record in check.expected_records],
                        "actual": [_record_projection(record) for record in check.actual_records],
                    }
                    for check in checks
                ],
            }
        )
        output_digest = _digest([report.to_projection() for report in reports])
        execution_id = derive_stable_id(
            "ctrlrecon",
            {
                "input_digest": input_digest,
                "configuration_digest": configuration_digest,
                "output_digest": output_digest,
            },
        )
        receipt = ExecutionReceipt(execution_id, input_digest, configuration_digest, output_digest)
        receipt_digest = _digest(receipt.to_dict())
        findings = tuple(
            _finding(
                report,
                dataset=dataset,
                locator=locator_by_report[report.report_id],
                receipt_digest=receipt_digest,
            )
            for report in reports
            if report.status is ReconciliationStatus.MISMATCH
        )
        return ReconciliationEvaluation(receipt, tuple(reports), findings)

    @classmethod
    def _evaluate_check(
        cls,
        check: ReconciliationCheck,
        *,
        dataset: DatasetReceipt,
    ) -> tuple[list[ReconciliationReport], dict[str, RecordLocator]]:
        expected = cls._prepare_side(check.expected_records, check.policy, dataset=dataset)
        actual = cls._prepare_side(check.actual_records, check.policy, dataset=dataset)
        all_grains = sorted(set(expected.groups) | set(actual.groups))
        globally_incomplete = expected.incomplete or actual.incomplete
        if not all_grains:
            all_grains = [()]
            globally_incomplete = True
        reports: list[ReconciliationReport] = []
        locators: dict[str, RecordLocator] = {}
        quantum = Decimal(1).scaleb(-check.policy.decimal_places)
        rounding = _ROUNDING[check.policy.rounding_policy]
        reasons = expected.exclusion_reasons + actual.exclusion_reasons

        for grain in all_grains:
            expected_group = expected.groups.get(grain, ())
            actual_group = actual.groups.get(grain, ())
            expected_total = _total(expected_group, quantum=quantum, rounding=rounding)
            actual_total = _total(actual_group, quantum=quantum, rounding=rounding)
            tolerance_basis = abs(expected_total or Decimal(0)) * check.policy.relative_tolerance
            tolerance = max(check.policy.absolute_tolerance, tolerance_basis).quantize(quantum, rounding=rounding)
            incomplete = globally_incomplete or not expected_group or not actual_group
            if incomplete:
                status = ReconciliationStatus.INCONCLUSIVE
                difference = None if expected_total is None or actual_total is None else actual_total - expected_total
            else:
                difference = actual_total - expected_total
                status = (
                    ReconciliationStatus.PASS
                    if abs(difference) <= tolerance
                    else ReconciliationStatus.MISMATCH
                )
            report_projection = {
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.dataset_version,
                "check_id": check.check_id,
                "kind": check.kind.value,
                "grain": dict(grain),
                "expected": None if expected_total is None else str(expected_total),
                "actual": None if actual_total is None else str(actual_total),
                "status": status.value,
                "policy_version": check.policy.version,
                "tolerance": str(tolerance),
                "expected_included_count": len(expected_group),
                "actual_included_count": len(actual_group),
                "expected_excluded_count": expected.excluded_count,
                "actual_excluded_count": actual.excluded_count,
                "exclusion_reasons": dict(sorted(reasons.items())),
            }
            report_id = derive_stable_id("recon", report_projection)
            expected_label, actual_label = _SIDE_LABELS[check.kind]
            report = ReconciliationReport(
                report_id=report_id,
                check_id=check.check_id,
                check_kind=check.kind,
                status=status,
                expected_label=expected_label,
                actual_label=actual_label,
                aggregation_grain=check.policy.aggregation_grain,
                grain_values=grain,
                expected=expected_total,
                actual=actual_total,
                difference=difference,
                tolerance=tolerance,
                expected_included_count=len(expected_group),
                actual_included_count=len(actual_group),
                expected_excluded_count=expected.excluded_count,
                actual_excluded_count=actual.excluded_count,
                exclusion_reasons=tuple(sorted(reasons.items())),
                currency=check.policy.currency,
                timezone=check.policy.timezone,
                fiscal_period=check.policy.fiscal_period,
                rounding_policy=check.policy.rounding_policy,
                policy_version=check.policy.version,
            )
            reports.append(report)
            locator = next(
                (record.locator for record in (*actual_group, *expected_group)),
                _fallback_locator(dataset),
            )
            locators[report_id] = locator
        return reports, locators

    @staticmethod
    def _prepare_side(
        records: Sequence[ReconciliationRecord],
        policy: ReconciliationPolicy,
        *,
        dataset: DatasetReceipt,
    ) -> _PreparedSide:
        groups: defaultdict[tuple[tuple[str, str], ...], list[ReconciliationRecord]] = defaultdict(list)
        reasons: Counter[str] = Counter()
        excluded = 0
        incomplete = False
        expected_dimensions = set(policy.aggregation_grain)
        for record in records:
            if not isinstance(record, ReconciliationRecord):
                raise BusinessControllingReconciliationError("controlling_reconciliation_record_invalid")
            if not isinstance(record.included, bool):
                raise BusinessControllingReconciliationError("controlling_reconciliation_record_invalid")
            try:
                locator = RecordLocator.from_mapping(record.locator.to_dict())
            except (AttributeError, ValueError) as exc:
                raise BusinessControllingReconciliationError("controlling_reconciliation_locator_invalid") from exc
            if locator.source_version != dataset.dataset_version:
                raise BusinessControllingReconciliationError("controlling_reconciliation_locator_scope_mismatch")
            try:
                dimensions = dict(record.dimensions)
            except (TypeError, ValueError) as exc:
                raise BusinessControllingReconciliationError("controlling_reconciliation_grain_invalid") from exc
            if len(dimensions) != len(record.dimensions) or set(dimensions) != expected_dimensions:
                excluded += 1
                reasons["mixed_or_missing_grain"] += 1
                incomplete = True
                continue
            if not record.included:
                if record.exclusion_reason not in policy.allowed_exclusion_reasons:
                    incomplete = True
                    reasons["undeclared_exclusion"] += 1
                else:
                    reasons[record.exclusion_reason] += 1
                excluded += 1
                continue
            if record.exclusion_reason is not None:
                raise BusinessControllingReconciliationError("controlling_reconciliation_exclusion_invalid")
            if (
                record.amount is None
                or not isinstance(record.amount, Decimal)
                or not record.amount.is_finite()
                or record.currency != policy.currency
                or record.timezone != policy.timezone
                or record.fiscal_period != policy.fiscal_period
            ):
                excluded += 1
                reasons["incomplete_or_mixed_metadata"] += 1
                incomplete = True
                continue
            grain = tuple((field, dimensions[field]) for field in policy.aggregation_grain)
            if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for _field, value in grain):
                raise BusinessControllingReconciliationError("controlling_reconciliation_grain_value_invalid")
            groups[grain].append(record)
        return _PreparedSide({key: tuple(value) for key, value in groups.items()}, excluded, reasons, incomplete)


def _total(
    records: Sequence[ReconciliationRecord],
    *,
    quantum: Decimal,
    rounding: str,
) -> Decimal | None:
    if not records:
        return None
    return sum((record.amount for record in records if record.amount is not None), Decimal(0)).quantize(
        quantum,
        rounding=rounding,
    )


def _record_projection(record: ReconciliationRecord) -> dict[str, object]:
    return {
        "locator": record.locator.to_dict(),
        "amount": None if record.amount is None else str(record.amount),
        "currency": record.currency,
        "timezone": record.timezone,
        "fiscal_period": record.fiscal_period,
        "dimensions": dict(record.dimensions),
        "included": record.included,
        "exclusion_reason": record.exclusion_reason,
    }


def _fallback_locator(dataset: DatasetReceipt) -> RecordLocator:
    return RecordLocator("csv", dataset.dataset_version, "csv_row", "row_1")


def _finding(
    report: ReconciliationReport,
    *,
    dataset: DatasetReceipt,
    locator: RecordLocator,
    receipt_digest: str,
) -> BusinessFinding:
    evidence_digest = _digest(report.to_projection())
    return BusinessFinding(
        CONTRACT_VERSION,
        derive_stable_id(
            "finding",
            {
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.dataset_version,
                "report_id": report.report_id,
                "evidence_digest": evidence_digest,
            },
        ),
        dataset.dataset_id,
        dataset.dataset_version,
        FindingKind.RECONCILIATION_MISMATCH,
        FindingSeverity.HIGH,
        f"reconcile_{report.check_kind.value}",
        report.policy_version,
        locator,
        evidence_digest,
        None,
        FindingDisposition.OPEN,
        receipt_digest,
    )


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise BusinessControllingReconciliationError("controlling_reconciliation_projection_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingReconciliationError",
    "BusinessControllingReconciliationService",
    "ReconciliationCheck",
    "ReconciliationCheckKind",
    "ReconciliationEvaluation",
    "ReconciliationPolicy",
    "ReconciliationRecord",
    "ReconciliationReport",
    "ReconciliationStatus",
]
