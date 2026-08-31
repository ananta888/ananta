"""Approved deterministic controlling rules with bounded, content-free evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Protocol

from agent.services.business_controlling_import_service import MappingConfirmation
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
_RULE_IDS = frozenset(
    {
        "required_field",
        "exact_duplicate_invoice",
        "similar_invoice_candidate",
        "negative_amount",
        "zero_amount",
        "invalid_period",
        "missing_cost_center",
        "unknown_cost_center",
        "currency_mismatch",
        "approval_limit_breach",
        "account_cost_center_mismatch",
    }
)


class BusinessControllingRuleError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class RuleCatalogApprovalPort(Protocol):
    def is_approved(
        self,
        *,
        tenant_id: str,
        project_id: str,
        catalog_id: str,
        catalog_version: str,
        catalog_digest: str,
    ) -> bool: ...


@dataclass(frozen=True)
class ControllingRuleCatalog:
    tenant_id: str
    project_id: str
    catalog_id: str
    version: str
    base_currency: str
    required_fields: tuple[str, ...]
    known_cost_centers: tuple[str, ...]
    approval_limit: Decimal
    allow_negative_amounts: bool = False
    allow_zero_amounts: bool = False
    invoice_similarity_threshold: Decimal = Decimal("0.90")
    account_cost_center_constraints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    enabled_rules: tuple[str, ...] = tuple(sorted(_RULE_IDS))

    def __post_init__(self) -> None:
        identifiers = (
            self.tenant_id,
            self.project_id,
            self.catalog_id,
            self.version,
            *self.required_fields,
            *self.known_cost_centers,
            *self.enabled_rules,
        )
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in identifiers):
            raise BusinessControllingRuleError("controlling_rule_catalog_identifier_invalid")
        if not re.fullmatch(r"[A-Z]{3}", self.base_currency):
            raise BusinessControllingRuleError("controlling_rule_catalog_currency_invalid")
        if (
            not self.required_fields
            or len(set(self.required_fields)) != len(self.required_fields)
            or len(set(self.known_cost_centers)) != len(self.known_cost_centers)
            or not set(self.enabled_rules).issubset(_RULE_IDS)
            or len(set(self.enabled_rules)) != len(self.enabled_rules)
        ):
            raise BusinessControllingRuleError("controlling_rule_catalog_shape_invalid")
        if not self.approval_limit.is_finite() or self.approval_limit < 0:
            raise BusinessControllingRuleError("controlling_rule_catalog_approval_limit_invalid")
        if (
            not self.invoice_similarity_threshold.is_finite()
            or not Decimal("0.50") <= self.invoice_similarity_threshold < Decimal("1")
        ):
            raise BusinessControllingRuleError("controlling_rule_catalog_similarity_invalid")
        accounts: set[str] = set()
        for account, cost_centers in self.account_cost_center_constraints:
            if (
                not _IDENTIFIER.fullmatch(account)
                or account in accounts
                or not cost_centers
                or len(set(cost_centers)) != len(cost_centers)
                or any(not _IDENTIFIER.fullmatch(value) for value in cost_centers)
                or not set(cost_centers).issubset(self.known_cost_centers)
            ):
                raise BusinessControllingRuleError("controlling_rule_catalog_constraint_invalid")
            accounts.add(account)

    @property
    def digest(self) -> str:
        return _digest(self.to_projection())

    def to_projection(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "catalog_id": self.catalog_id,
            "version": self.version,
            "base_currency": self.base_currency,
            "required_fields": list(self.required_fields),
            "known_cost_centers": list(self.known_cost_centers),
            "approval_limit": str(self.approval_limit),
            "allow_negative_amounts": self.allow_negative_amounts,
            "allow_zero_amounts": self.allow_zero_amounts,
            "invoice_similarity_threshold": str(self.invoice_similarity_threshold),
            "account_cost_center_constraints": [
                [account, list(cost_centers)]
                for account, cost_centers in self.account_cost_center_constraints
            ],
            "enabled_rules": list(self.enabled_rules),
        }


@dataclass(frozen=True)
class ControllingRecord:
    locator: RecordLocator
    values: Mapping[str, object]


@dataclass(frozen=True)
class DeterministicRuleFinding:
    finding: BusinessFinding
    parameters: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DeterministicRuleEvaluation:
    catalog_digest: str
    execution_receipt: ExecutionReceipt
    findings: tuple[DeterministicRuleFinding, ...]


@dataclass(frozen=True)
class _Candidate:
    rule_id: str
    severity: FindingSeverity
    locator: RecordLocator
    parameters: tuple[tuple[str, str], ...]
    record_digest: str


class BusinessControllingRuleEvaluator:
    """Evaluates admitted config locally and never mutates source records."""

    MAX_RECORDS = 100_000

    def __init__(self, approvals: RuleCatalogApprovalPort) -> None:
        self._approvals = approvals

    def evaluate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        dataset: DatasetReceipt,
        profile_digest: str,
        mapping: MappingConfirmation,
        catalog: ControllingRuleCatalog,
        records: Sequence[ControllingRecord],
    ) -> DeterministicRuleEvaluation:
        self._validate_scope(tenant_id, project_id, dataset, profile_digest, mapping, catalog, records)
        if not self._approvals.is_approved(
            tenant_id=tenant_id,
            project_id=project_id,
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.version,
            catalog_digest=catalog.digest,
        ):
            raise BusinessControllingRuleError("controlling_rule_catalog_not_approved")

        source_mapping = dict(mapping.column_mapping)
        normalized = tuple(
            (
                record,
                {canonical: record.values.get(source) for source, canonical in source_mapping.items()},
                _record_digest(record),
            )
            for record in records
        )
        candidates = self._evaluate_records(normalized, dataset=dataset, catalog=catalog)
        input_digest = _digest(
            {
                "dataset": dataset.to_dict(),
                "profile_digest": profile_digest,
                "mapping_confirmation_digest": mapping.confirmation_digest,
                "records": [
                    {"locator": record.locator.to_dict(), "record_digest": record_digest}
                    for record, _values, record_digest in normalized
                ],
            }
        )
        output_digest = _digest([_candidate_projection(candidate) for candidate in candidates])
        execution_id = derive_stable_id(
            "ctrlrun",
            {
                "input_digest": input_digest,
                "configuration_digest": catalog.digest,
                "output_digest": output_digest,
            },
        )
        receipt = ExecutionReceipt(execution_id, input_digest, catalog.digest, output_digest)
        receipt_digest = _digest(receipt.to_dict())
        findings = tuple(
            DeterministicRuleFinding(
                finding=_business_finding(
                    candidate,
                    dataset=dataset,
                    rule_version=catalog.version,
                    receipt_digest=receipt_digest,
                ),
                parameters=candidate.parameters,
            )
            for candidate in candidates
        )
        return DeterministicRuleEvaluation(catalog.digest, receipt, findings)

    @classmethod
    def _validate_scope(
        cls,
        tenant_id: str,
        project_id: str,
        dataset: DatasetReceipt,
        profile_digest: str,
        mapping: MappingConfirmation,
        catalog: ControllingRuleCatalog,
        records: Sequence[ControllingRecord],
    ) -> None:
        if catalog.tenant_id != tenant_id or catalog.project_id != project_id:
            raise BusinessControllingRuleError("controlling_rule_catalog_scope_mismatch")
        if len(records) > cls.MAX_RECORDS:
            raise BusinessControllingRuleError("controlling_rule_record_budget_exceeded")
        if not re.fullmatch(r"[0-9a-f]{64}", profile_digest) or mapping.profile_digest != profile_digest:
            raise BusinessControllingRuleError("controlling_rule_mapping_profile_mismatch")
        mapping_projection = {
            "profile_digest": mapping.profile_digest,
            "column_mapping": mapping.column_mapping,
            "confirmed_by": mapping.confirmed_by,
        }
        if mapping.confirmation_digest != _digest(mapping_projection):
            raise BusinessControllingRuleError("controlling_rule_mapping_confirmation_invalid")
        expected_dataset_mapping = {canonical: source for source, canonical in mapping.column_mapping}
        if dict(dataset.column_mapping) != expected_dataset_mapping:
            raise BusinessControllingRuleError("controlling_rule_dataset_mapping_mismatch")
        try:
            period_start = date.fromisoformat(dataset.period_start)
            period_end = date.fromisoformat(dataset.period_end)
        except ValueError as exc:
            raise BusinessControllingRuleError("controlling_rule_dataset_period_invalid") from exc
        if period_end < period_start:
            raise BusinessControllingRuleError("controlling_rule_dataset_period_invalid")
        for record in records:
            validated = RecordLocator.from_mapping(record.locator.to_dict())
            if validated.source_version != dataset.dataset_version or validated.source_kind not in {"csv", "xlsx"}:
                raise BusinessControllingRuleError("controlling_rule_locator_scope_mismatch")
            if not isinstance(record.values, Mapping) or any(not isinstance(key, str) for key in record.values):
                raise BusinessControllingRuleError("controlling_rule_record_invalid")

    @staticmethod
    def _evaluate_records(
        normalized: Sequence[tuple[ControllingRecord, Mapping[str, object], str]],
        *,
        dataset: DatasetReceipt,
        catalog: ControllingRuleCatalog,
    ) -> tuple[_Candidate, ...]:
        findings: list[_Candidate] = []
        exact_invoices: dict[str, RecordLocator] = {}
        invoice_signatures: dict[str, dict[str, RecordLocator]] = {}
        constraints = dict(catalog.account_cost_center_constraints)
        enabled = frozenset(catalog.enabled_rules)

        for record, values, record_digest in normalized:
            locator = record.locator
            for field in catalog.required_fields:
                if "required_field" in enabled and _missing(values.get(field)):
                    findings.append(
                        _candidate(
                            "required_field",
                            FindingSeverity.HIGH,
                            locator,
                            {"field": field},
                            record_digest,
                        )
                    )

            invoice = _normalized_invoice(values.get("invoice_id"))
            if invoice:
                previous = exact_invoices.get(invoice)
                if previous is not None and "exact_duplicate_invoice" in enabled:
                    findings.append(
                        _candidate(
                            "exact_duplicate_invoice",
                            FindingSeverity.HIGH,
                            locator,
                            {"related_locator": previous.locator},
                            record_digest,
                        )
                    )
                elif previous is None:
                    similar = _similar_invoice(
                        invoice,
                        invoice_signatures,
                        threshold=float(catalog.invoice_similarity_threshold),
                    )
                    if similar is not None and "similar_invoice_candidate" in enabled:
                        _related_invoice, related_locator, score = similar
                        findings.append(
                            _candidate(
                                "similar_invoice_candidate",
                                FindingSeverity.MEDIUM,
                                locator,
                                {
                                    "related_locator": related_locator.locator,
                                    "similarity_threshold": str(catalog.invoice_similarity_threshold),
                                    "similarity_score": f"{score:.6f}",
                                },
                                record_digest,
                            )
                        )
                    exact_invoices[invoice] = locator
                    for signature in _invoice_signatures(invoice):
                        invoice_signatures.setdefault(signature, {}).setdefault(invoice, locator)

            amount = _decimal(values.get("amount"))
            if amount is not None:
                if amount < 0 and not catalog.allow_negative_amounts and "negative_amount" in enabled:
                    findings.append(
                        _candidate(
                            "negative_amount",
                            FindingSeverity.HIGH,
                            locator,
                            {"allowed": "false"},
                            record_digest,
                        )
                    )
                if amount == 0 and not catalog.allow_zero_amounts and "zero_amount" in enabled:
                    findings.append(
                        _candidate("zero_amount", FindingSeverity.MEDIUM, locator, {"allowed": "false"}, record_digest)
                    )
                if amount > catalog.approval_limit and "approval_limit_breach" in enabled:
                    findings.append(
                        _candidate(
                            "approval_limit_breach",
                            FindingSeverity.HIGH,
                            locator,
                            {"approval_limit": str(catalog.approval_limit), "currency": catalog.base_currency},
                            record_digest,
                        )
                    )

            period = _date(values.get("period"))
            if "invalid_period" in enabled and (
                period is None
                or period < date.fromisoformat(dataset.period_start)
                or period > date.fromisoformat(dataset.period_end)
            ):
                findings.append(
                    _candidate(
                        "invalid_period",
                        FindingSeverity.HIGH,
                        locator,
                        {"period_start": dataset.period_start, "period_end": dataset.period_end},
                        record_digest,
                    )
                )

            cost_center = _text(values.get("cost_center"))
            if not cost_center and "missing_cost_center" in enabled:
                findings.append(_candidate("missing_cost_center", FindingSeverity.HIGH, locator, {}, record_digest))
            elif (
                cost_center
                and cost_center not in catalog.known_cost_centers
                and "unknown_cost_center" in enabled
            ):
                findings.append(
                    _candidate(
                        "unknown_cost_center",
                        FindingSeverity.HIGH,
                        locator,
                        {"catalog_size": str(len(catalog.known_cost_centers))},
                        record_digest,
                    )
                )

            currency = _text(values.get("currency"))
            if currency != catalog.base_currency and "currency_mismatch" in enabled:
                findings.append(
                    _candidate(
                        "currency_mismatch",
                        FindingSeverity.HIGH,
                        locator,
                        {"expected_currency": catalog.base_currency},
                        record_digest,
                    )
                )

            account = _text(values.get("account"))
            allowed_centers = constraints.get(account or "")
            if (
                allowed_centers is not None
                and cost_center not in allowed_centers
                and "account_cost_center_mismatch" in enabled
            ):
                findings.append(
                    _candidate(
                        "account_cost_center_mismatch",
                        FindingSeverity.HIGH,
                        locator,
                        {"account": account or "missing", "allowed_cost_centers": ",".join(allowed_centers)},
                        record_digest,
                    )
                )
        return tuple(findings)


def _candidate(
    rule_id: str,
    severity: FindingSeverity,
    locator: RecordLocator,
    parameters: Mapping[str, str],
    record_digest: str,
) -> _Candidate:
    return _Candidate(rule_id, severity, locator, tuple(sorted(parameters.items())), record_digest)


def _candidate_projection(candidate: _Candidate) -> dict[str, object]:
    return {
        "rule_id": candidate.rule_id,
        "severity": candidate.severity.value,
        "locator": candidate.locator.to_dict(),
        "parameters": dict(candidate.parameters),
        "record_digest": candidate.record_digest,
    }


def _business_finding(
    candidate: _Candidate,
    *,
    dataset: DatasetReceipt,
    rule_version: str,
    receipt_digest: str,
) -> BusinessFinding:
    evidence_digest = _digest(_candidate_projection(candidate))
    finding_id = derive_stable_id(
        "finding",
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "rule_id": candidate.rule_id,
            "rule_version": rule_version,
            "locator": candidate.locator.to_dict(),
            "evidence_digest": evidence_digest,
        },
    )
    return BusinessFinding(
        CONTRACT_VERSION,
        finding_id,
        dataset.dataset_id,
        dataset.dataset_version,
        FindingKind.DETERMINISTIC_VIOLATION,
        candidate.severity,
        candidate.rule_id,
        rule_version,
        candidate.locator,
        evidence_digest,
        None,
        FindingDisposition.OPEN,
        receipt_digest,
    )


def _record_digest(record: ControllingRecord) -> str:
    return _digest(
        {
            "locator": record.locator.to_dict(),
            "values": {key: _canonical_value(value) for key, value in sorted(record.values.items())},
        }
    )


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BusinessControllingRuleError("controlling_rule_record_invalid")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BusinessControllingRuleError("controlling_rule_record_invalid")
        return value
    if isinstance(value, date):
        return value.isoformat()
    raise BusinessControllingRuleError("controlling_rule_record_invalid")


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
        raise BusinessControllingRuleError("controlling_rule_projection_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _normalized_invoice(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    if len(normalized) > 128:
        raise BusinessControllingRuleError("controlling_rule_invoice_identifier_invalid")
    return normalized or None


def _invoice_signatures(invoice: str) -> frozenset[str]:
    return frozenset({invoice, *(invoice[:index] + invoice[index + 1 :] for index in range(len(invoice)))})


def _similar_invoice(
    invoice: str,
    index: Mapping[str, Mapping[str, RecordLocator]],
    *,
    threshold: float,
) -> tuple[str, RecordLocator, float] | None:
    candidates: dict[str, RecordLocator] = {}
    for signature in _invoice_signatures(invoice):
        candidates.update(index.get(signature, {}))
    ranked = sorted(
        (
            (SequenceMatcher(None, invoice, candidate, autojunk=False).ratio(), candidate, locator)
            for candidate, locator in candidates.items()
            if candidate != invoice
        ),
        key=lambda item: (-item[0], item[1], item[2].locator),
    )
    if not ranked or ranked[0][0] < threshold:
        return None
    score, candidate, locator = ranked[0]
    return candidate, locator, score


__all__ = [
    "BusinessControllingRuleError",
    "BusinessControllingRuleEvaluator",
    "ControllingRecord",
    "ControllingRuleCatalog",
    "DeterministicRuleEvaluation",
    "DeterministicRuleFinding",
    "RuleCatalogApprovalPort",
]
