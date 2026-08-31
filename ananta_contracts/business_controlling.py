"""Strict, versioned contracts for Hub-owned business-data controlling.

The models intentionally describe receipts and bounded references, never raw
business values.  Import, persistence, rule evaluation and UI presentation
remain separate services that consume these immutable contracts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

CONTRACT_VERSION = "ananta.business-controlling.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class BusinessControllingContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class FindingKind(str, Enum):
    DETERMINISTIC_VIOLATION = "deterministic_violation"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    STATISTICAL_ANOMALY = "statistical_anomaly"
    ADVISORY_EXPLANATION = "advisory_explanation"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingDisposition(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    NEEDS_DATA = "needs_data"
    ACCEPTED_EXCEPTION = "accepted_exception"


def _closed(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields or any(not isinstance(key, str) for key in value):
        raise BusinessControllingContractError("business_controlling_shape_invalid")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise BusinessControllingContractError("business_controlling_identifier_invalid")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise BusinessControllingContractError("business_controlling_digest_invalid")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise BusinessControllingContractError("business_controlling_not_canonical") from exc


def derive_stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    """Derive an opaque stable ID without embedding sensitive business data."""

    if not _IDENTIFIER.fullmatch(prefix):
        raise BusinessControllingContractError("business_controlling_identifier_invalid")
    return f"{prefix}_{hashlib.sha256(_canonical(value)).hexdigest()}"


@dataclass(frozen=True)
class RecordLocator:
    source_kind: str
    source_version: str
    locator_kind: str
    locator: str

    @classmethod
    def from_mapping(cls, value: Any) -> "RecordLocator":
        data = _closed(value, frozenset({"source_kind", "source_version", "locator_kind", "locator"}))
        locator_kind = data["locator_kind"]
        if locator_kind not in {"csv_row", "xlsx_range"}:
            raise BusinessControllingContractError("business_controlling_locator_kind_invalid")
        locator = _identifier(data["locator"])
        return cls(_identifier(data["source_kind"]), _identifier(data["source_version"]), locator_kind, locator)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetReceipt:
    """Versioned, content-addressed dataset metadata without raw records."""

    contract_version: str
    dataset_id: str
    dataset_version: str
    source_digest: str
    period_start: str
    period_end: str
    currency: str
    column_mapping: tuple[tuple[str, str], ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "DatasetReceipt":
        data = _closed(
            value,
            frozenset(
                {
                    "contract_version", "dataset_id", "dataset_version", "source_digest", "period_start", "period_end",
                    "currency", "column_mapping",
                }
            ),
        )
        if data["contract_version"] != CONTRACT_VERSION:
            raise BusinessControllingContractError("business_controlling_contract_version_invalid")
        if not isinstance(data["period_start"], str) or not isinstance(data["period_end"], str):
            raise BusinessControllingContractError("business_controlling_period_invalid")
        if not isinstance(data["currency"], str) or not _CURRENCY.fullmatch(data["currency"]):
            raise BusinessControllingContractError("business_controlling_currency_invalid")
        mapping = data["column_mapping"]
        if not isinstance(mapping, Mapping) or not mapping:
            raise BusinessControllingContractError("business_controlling_mapping_invalid")
        normalized_mapping = tuple(sorted((_identifier(key), _identifier(item)) for key, item in mapping.items()))
        return cls(
            CONTRACT_VERSION,
            _identifier(data["dataset_id"]),
            _identifier(data["dataset_version"]),
            _digest(data["source_digest"]),
            data["period_start"],
            data["period_end"],
            data["currency"],
            normalized_mapping,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["column_mapping"] = dict(self.column_mapping)
        return payload


@dataclass(frozen=True)
class ExecutionReceipt:
    execution_id: str
    input_digest: str
    configuration_digest: str
    output_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "ExecutionReceipt":
        data = _closed(value, frozenset({"execution_id", "input_digest", "configuration_digest", "output_digest"}))
        return cls(
            _identifier(data["execution_id"]),
            _digest(data["input_digest"]),
            _digest(data["configuration_digest"]),
            _digest(data["output_digest"]),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BusinessFinding:
    contract_version: str
    finding_id: str
    dataset_id: str
    dataset_version: str
    kind: FindingKind
    severity: FindingSeverity
    rule_id: str
    rule_version: str
    locator: RecordLocator
    evidence_digest: str
    confidence: float | None
    disposition: FindingDisposition
    execution_receipt_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "BusinessFinding":
        fields = frozenset(
            {
                "contract_version", "finding_id", "dataset_id", "dataset_version", "kind", "severity", "rule_id",
                "rule_version", "locator", "evidence_digest", "confidence", "disposition", "execution_receipt_digest",
            }
        )
        data = _closed(value, fields)
        if data["contract_version"] != CONTRACT_VERSION:
            raise BusinessControllingContractError("business_controlling_contract_version_invalid")
        confidence = data["confidence"]
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
        ):
            raise BusinessControllingContractError("business_controlling_confidence_invalid")
        try:
            kind = FindingKind(data["kind"])
            severity = FindingSeverity(data["severity"])
            disposition = FindingDisposition(data["disposition"])
        except ValueError as exc:
            raise BusinessControllingContractError("business_controlling_enum_invalid") from exc
        return cls(
            CONTRACT_VERSION,
            _identifier(data["finding_id"]),
            _identifier(data["dataset_id"]),
            _identifier(data["dataset_version"]),
            kind,
            severity,
            _identifier(data["rule_id"]),
            _identifier(data["rule_version"]),
            RecordLocator.from_mapping(data["locator"]),
            _digest(data["evidence_digest"]),
            None if confidence is None else float(confidence),
            disposition,
            _digest(data["execution_receipt_digest"]),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["severity"] = self.severity.value
        payload["disposition"] = self.disposition.value
        return payload


__all__ = [
    "BusinessControllingContractError",
    "BusinessFinding",
    "CONTRACT_VERSION",
    "DatasetReceipt",
    "ExecutionReceipt",
    "FindingDisposition",
    "FindingKind",
    "FindingSeverity",
    "RecordLocator",
    "derive_stable_id",
]
