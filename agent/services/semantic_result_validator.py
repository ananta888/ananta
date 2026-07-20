"""Hub-side admission of raw-free semantic validator reports.

Validation is deterministic; authority checks are explicit fail-closed ports.
This service does not schedule work or alter the Ordinary media baseline.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

SCHEMA = "ananta.semantic-validator-report.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9._~+/-]{16,1024}={0,2}$")


class SemanticValidatorAuthorityPort(Protocol):
    def verify_signature(self, report: Mapping[str, Any], canonical_unsigned: bytes) -> bool: ...
    def valid_audience(self, audience: str, session_id: str) -> bool: ...
    def valid_validator_lease(
        self, lease_id: str, validator_id: str, session_id: str, epoch: int, now_ms: int
    ) -> bool: ...


@dataclass(frozen=True)
class SemanticReportAdmission:
    admissible: bool
    verdict: str
    reason_code: str
    session_id: str | None = None
    validator_id: str | None = None
    validator_lease_id: str | None = None
    sequence: int | None = None
    ordinary_baseline_affected: bool = False


class SemanticResultValidator:
    def __init__(self, authority: SemanticValidatorAuthorityPort | None) -> None:
        self._authority = authority

    def admit(self, raw: object, *, now_ms: int) -> SemanticReportAdmission:
        try:
            report = _validate_shape(raw, now_ms=now_ms)
        except ValueError as exc:
            return SemanticReportAdmission(False, "ignored", str(exc))
        if self._authority is None:
            return _rejected(report, "validator_authority_unavailable")
        unsigned = {key: value for key, value in report.items() if key != "signature"}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if not self._authority.verify_signature(report, canonical):
            return _rejected(report, "validator_signature_invalid")
        if not self._authority.valid_audience(report["audience"], report["session_id"]):
            return _rejected(report, "validator_audience_invalid")
        if not self._authority.valid_validator_lease(
            report["validator_lease_id"], report["validator_id"], report["session_id"], report["epoch"], now_ms
        ):
            return _rejected(report, "validator_lease_invalid")
        return SemanticReportAdmission(
            True, report["verdict"], "validator_report_admitted", report["session_id"],
            report["validator_id"], report["validator_lease_id"], report["sequence"], False,
        )

    def reconcile(self, admissions: list[SemanticReportAdmission]) -> str:
        admitted = [item.verdict for item in admissions if item.admissible]
        if not admitted:
            return "ordinary_fallback"
        if "conflict" in admitted or len(set(admitted)) > 1:
            return "semantic_recovery"
        return "semantic_accept" if admitted[0] == "pass" else "ordinary_fallback"


def _validate_shape(raw: object, *, now_ms: int) -> Mapping[str, Any]:
    required = {
        "schema", "report_id", "session_id", "contract_id", "validator_lease_id", "validator_id",
        "validator_role", "audience", "epoch", "sequence", "input_digest", "output_digest",
        "criteria", "verdict", "observed_at_ms", "expires_at_ms", "signature",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise ValueError("validator_schema_invalid")
    if raw["schema"] != SCHEMA or raw["validator_role"] != "validator" or raw["audience"] != "hub":
        raise ValueError("validator_schema_invalid")
    for field in ("report_id", "session_id", "contract_id", "validator_lease_id", "validator_id"):
        if not isinstance(raw[field], str) or not _ID.fullmatch(raw[field]):
            raise ValueError("validator_binding_invalid")
    for field in ("input_digest", "output_digest"):
        if not isinstance(raw[field], str) or not _DIGEST.fullmatch(raw[field]):
            raise ValueError("validator_binding_invalid")
    for field, minimum in (("epoch", 1), ("sequence", 0), ("observed_at_ms", 0), ("expires_at_ms", 1)):
        if isinstance(raw[field], bool) or not isinstance(raw[field], int) or raw[field] < minimum:
            raise ValueError("validator_binding_invalid")
    if raw["expires_at_ms"] <= now_ms or raw["expires_at_ms"] - raw["observed_at_ms"] > 60_000:
        raise ValueError("validator_report_expired")
    criteria = raw["criteria"]
    criteria_keys = {"schema_valid", "binding_valid", "quality_score", "drift_score", "deadline_met"}
    if not isinstance(criteria, Mapping) or set(criteria) != criteria_keys:
        raise ValueError("validator_criteria_invalid")
    if any(not isinstance(criteria[field], bool) for field in ("schema_valid", "binding_valid", "deadline_met")):
        raise ValueError("validator_criteria_invalid")
    for field in ("quality_score", "drift_score"):
        value = criteria[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("validator_criteria_invalid")
    if raw["verdict"] not in {"pass", "fail", "conflict"}:
        raise ValueError("validator_verdict_invalid")
    expected = (
        criteria["schema_valid"] and criteria["binding_valid"] and criteria["deadline_met"]
        and criteria["quality_score"] >= 0.75 and criteria["drift_score"] <= 0.15
    )
    if raw["verdict"] != "conflict" and (raw["verdict"] == "pass") is not expected:
        raise ValueError("validator_verdict_inconsistent")
    if not isinstance(raw["signature"], str) or not _SIGNATURE.fullmatch(raw["signature"]):
        raise ValueError("validator_signature_invalid")
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 32 * 1024 or any(token in raw for token in ("frame", "pixels", "residual", "geometry")):
        raise ValueError("validator_content_leak")
    return raw


def _rejected(report: Mapping[str, Any], reason: str) -> SemanticReportAdmission:
    return SemanticReportAdmission(
        False, "ignored", reason, report["session_id"], report["validator_id"],
        report["validator_lease_id"], report["sequence"], False,
    )
