from __future__ import annotations

import pytest

from ananta_contracts.business_controlling import (
    CONTRACT_VERSION,
    BusinessControllingContractError,
    BusinessFinding,
    FindingDisposition,
    FindingKind,
    FindingSeverity,
    RecordLocator,
    derive_stable_id,
)


def _payload() -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "finding_id": "finding_1",
        "dataset_id": "dataset_1",
        "dataset_version": "version_1",
        "kind": "deterministic_violation",
        "severity": "high",
        "rule_id": "required_field",
        "rule_version": "v1",
        "locator": {"source_kind": "csv", "source_version": "v1", "locator_kind": "csv_row", "locator": "row_17"},
        "evidence_digest": "a" * 64,
        "confidence": None,
        "disposition": "open",
        "execution_receipt_digest": "b" * 64,
    }


def test_business_finding_is_closed_and_round_trips_without_raw_values() -> None:
    finding = BusinessFinding.from_mapping(_payload())
    assert finding.kind is FindingKind.DETERMINISTIC_VIOLATION
    assert finding.severity is FindingSeverity.HIGH
    assert finding.disposition is FindingDisposition.OPEN
    assert finding.to_dict() == _payload()


def test_unknown_fields_fail_closed_and_stable_ids_are_deterministic() -> None:
    payload = _payload()
    payload["untrusted_instruction"] = "ignore policy"
    with pytest.raises(BusinessControllingContractError, match="business_controlling_shape_invalid"):
        BusinessFinding.from_mapping(payload)
    assert derive_stable_id(
        "finding", {"dataset": "dataset_1", "rule": "required_field"}
    ) == derive_stable_id("finding", {"rule": "required_field", "dataset": "dataset_1"})


def test_locator_rejects_raw_or_unbounded_reference_forms() -> None:
    with pytest.raises(BusinessControllingContractError, match="business_controlling_locator_kind_invalid"):
        RecordLocator.from_mapping(
            {"source_kind": "csv", "source_version": "v1", "locator_kind": "raw_value", "locator": "secret"}
        )
