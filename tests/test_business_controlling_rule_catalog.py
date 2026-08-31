from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from agent.services.business_controlling_import_service import (
    BusinessControllingImportService,
    ColumnProfile,
    TabularProfile,
)
from agent.services.business_controlling_rule_catalog import (
    BusinessControllingRuleError,
    BusinessControllingRuleEvaluator,
    ControllingRecord,
    ControllingRuleCatalog,
)
from ananta_contracts.business_controlling import CONTRACT_VERSION, DatasetReceipt, RecordLocator


class _Approvals:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.requests: list[dict[str, str]] = []

    def is_approved(self, **request: str) -> bool:
        self.requests.append(request)
        return self.approved


def _catalog(**changes: object) -> ControllingRuleCatalog:
    values = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "catalog_id": "default",
        "version": "v1",
        "base_currency": "EUR",
        "required_fields": ("invoice_id", "amount", "period", "currency", "cost_center", "account"),
        "known_cost_centers": ("CC-1", "CC-2"),
        "approval_limit": Decimal("100.00"),
        "invoice_similarity_threshold": Decimal("0.80"),
        "account_cost_center_constraints": (("A1", ("CC-1",)),),
    }
    values.update(changes)
    return ControllingRuleCatalog(**values)  # type: ignore[arg-type]


def _profile_and_mapping():
    columns = tuple(
        ColumnProfile(source, "text", 0, 0)
        for source in ("Invoice", "Amount", "Period", "Currency", "CostCenter", "Account")
    )
    profile = TabularProfile("srev-a", "a" * 64, 5, 0, columns, "b" * 64)
    mapping = BusinessControllingImportService.confirm_mapping(
        profile,
        {
            "Invoice": "invoice_id",
            "Amount": "amount",
            "Period": "period",
            "Currency": "currency",
            "CostCenter": "cost_center",
            "Account": "account",
        },
        confirmed_by="operator-a",
    )
    return profile, mapping


def _dataset(mapping) -> DatasetReceipt:
    return DatasetReceipt.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "dataset_id": "dataset-a",
            "dataset_version": "version-a",
            "source_digest": "c" * 64,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "currency": "EUR",
            "column_mapping": {canonical: source for source, canonical in mapping.column_mapping},
        }
    )


def _record(row: int, **values: object) -> ControllingRecord:
    return ControllingRecord(
        RecordLocator.from_mapping(
            {
                "source_kind": "csv",
                "source_version": "version-a",
                "locator_kind": "csv_row",
                "locator": f"row_{row}",
            }
        ),
        values,
    )


def _records() -> tuple[ControllingRecord, ...]:
    return (
        _record(
            2,
            Invoice="INV-100",
            Amount="10.00",
            Period="2026-01-05",
            Currency="EUR",
            CostCenter="CC-1",
            Account="A1",
        ),
        _record(
            3,
            Invoice="INV-100",
            Amount="-1.00",
            Period="invalid",
            Currency="USD",
            CostCenter="",
            Account="A1",
        ),
        _record(
            4,
            Invoice="INV-101",
            Amount="0",
            Period="2026-01-10",
            Currency="EUR",
            CostCenter="CC-X",
            Account="A1",
        ),
        _record(
            5,
            Invoice="INV-200",
            Amount="150.00",
            Period="2026-01-10",
            Currency="EUR",
            CostCenter="CC-1",
            Account="A1",
        ),
        _record(
            6,
            Invoice="",
            Amount="1.00",
            Period="2026-01-10",
            Currency="EUR",
            CostCenter="CC-1",
            Account="A1",
        ),
    )


def test_approved_catalog_emits_every_deterministic_rule_without_mutation_or_raw_values() -> None:
    profile, mapping = _profile_and_mapping()
    records = _records()
    original = tuple(dict(record.values) for record in records)
    approvals = _Approvals()
    evaluator = BusinessControllingRuleEvaluator(approvals)

    result = evaluator.evaluate(
        tenant_id="tenant-a",
        project_id="project-a",
        dataset=_dataset(mapping),
        profile_digest=profile.profile_digest,
        mapping=mapping,
        catalog=_catalog(),
        records=records,
    )

    rule_ids = {item.finding.rule_id for item in result.findings}
    assert rule_ids == {
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
    assert all(item.finding.rule_version == "v1" and item.parameters is not None for item in result.findings)
    assert all(item.finding.locator.locator.startswith("row_") for item in result.findings)
    assert tuple(dict(record.values) for record in records) == original
    assert "INV-100" not in repr(result)
    assert approvals.requests == [
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "catalog_id": "default",
            "catalog_version": "v1",
            "catalog_digest": _catalog().digest,
        }
    ]


def test_evaluation_is_offline_deterministic_and_distinguishes_duplicate_classes() -> None:
    profile, mapping = _profile_and_mapping()
    evaluator = BusinessControllingRuleEvaluator(_Approvals())
    arguments = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "dataset": _dataset(mapping),
        "profile_digest": profile.profile_digest,
        "mapping": mapping,
        "catalog": _catalog(),
        "records": _records(),
    }
    first = evaluator.evaluate(**arguments)
    second = evaluator.evaluate(**arguments)
    assert first == second
    duplicates = [
        (item.finding.rule_id, item.finding.locator.locator, dict(item.parameters))
        for item in first.findings
        if "duplicate" in item.finding.rule_id or "similar" in item.finding.rule_id
    ]
    assert ("exact_duplicate_invoice", "row_3", {"related_locator": "row_2"}) in duplicates
    assert any(
        rule_id == "similar_invoice_candidate"
        and locator == "row_4"
        and parameters["related_locator"] == "row_2"
        for rule_id, locator, parameters in duplicates
    )


def test_catalog_changes_are_digest_version_bound_and_require_approval() -> None:
    profile, mapping = _profile_and_mapping()
    catalog = _catalog()
    changed = replace(catalog, version="v2", approval_limit=Decimal("200.00"))
    assert changed.digest != catalog.digest
    with pytest.raises(BusinessControllingRuleError, match="catalog_not_approved"):
        BusinessControllingRuleEvaluator(_Approvals(False)).evaluate(
            tenant_id="tenant-a",
            project_id="project-a",
            dataset=_dataset(mapping),
            profile_digest=profile.profile_digest,
            mapping=mapping,
            catalog=changed,
            records=_records(),
        )


def test_unconfirmed_or_cross_scope_mapping_fails_closed_before_rules_run() -> None:
    profile, mapping = _profile_and_mapping()
    evaluator = BusinessControllingRuleEvaluator(_Approvals())
    with pytest.raises(BusinessControllingRuleError, match="mapping_confirmation_invalid"):
        evaluator.evaluate(
            tenant_id="tenant-a",
            project_id="project-a",
            dataset=_dataset(mapping),
            profile_digest=profile.profile_digest,
            mapping=replace(mapping, confirmation_digest="d" * 64),
            catalog=_catalog(),
            records=_records(),
        )
    with pytest.raises(BusinessControllingRuleError, match="catalog_scope_mismatch"):
        evaluator.evaluate(
            tenant_id="tenant-b",
            project_id="project-a",
            dataset=_dataset(mapping),
            profile_digest=profile.profile_digest,
            mapping=mapping,
            catalog=_catalog(),
            records=_records(),
        )
