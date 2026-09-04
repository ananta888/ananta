"""Dependency-free contracts for Hub-governed Python verification runs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment

VERIFICATION_ASSIGNMENT_SCHEMA = "ananta.verification-assignment.v1"
VERIFICATION_REPORT_SCHEMA = "ananta.verification-report.v1"
COUNTEREXAMPLE_SCHEMA = "ananta.counterexample.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class VerificationStatus(str, Enum):
    PASSED = "passed"
    PASSED_WITH_BOUNDED_SEARCH = "passed_with_bounded_search"
    COUNTEREXAMPLE_FOUND = "counterexample_found"
    FAILED_TO_REPRODUCE = "failed_to_reproduce"
    INCONCLUSIVE = "inconclusive"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    POLICY_DENIED = "policy_denied"
    TOOL_ERROR = "tool_error"


def canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


def counterexample_candidate_digest(value: Mapping[str, Any]) -> str:
    stable = {
        "property_ref": value.get("property_ref"),
        "target_symbol": value.get("target_symbol"),
        "concrete_arguments": value.get("concrete_arguments"),
        "expected_invariant": value.get("expected_invariant"),
        "reproduction_command": list(value.get("reproduction_command") or []),
    }
    return canonical_digest(stable)


def _require_digest(value: str, reason: str) -> None:
    if _DIGEST.fullmatch(str(value or "")) is None:
        raise ValueError(reason)


def _require_id(value: str, reason: str) -> None:
    if _ID.fullmatch(str(value or "")) is None:
        raise ValueError(reason)


def _closed_mapping(value: Mapping[str, Any], fields: set[str], reason: str) -> dict[str, Any]:
    raw = dict(value)
    if set(raw) != fields:
        raise ValueError(reason)
    return raw


def _json_concrete(value: Any) -> Any:
    """Return plain JSON data and reject symbolic/proxy/custom values."""

    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("verification_counterexample_not_concrete") from exc
    if type(value) not in {dict, list, str, int, float, bool, type(None)}:
        raise ValueError("verification_counterexample_not_concrete")
    return decoded


@dataclass(frozen=True, slots=True)
class VerificationBudgets:
    timeout_seconds: int
    max_cases: int
    max_targets: int
    max_output_bytes: int
    max_processes: int = 1
    memory_mb: int = 512

    def __post_init__(self) -> None:
        limits = (
            (self.timeout_seconds, 1, 3600),
            (self.max_cases, 1, 1_000_000),
            (self.max_targets, 1, 100),
            (self.max_output_bytes, 256, 10_000_000),
            (self.max_processes, 1, 4),
            (self.memory_mb, 64, 16_384),
        )
        if any(type(value) is not int or not low <= value <= high for value, low, high in limits):
            raise ValueError("verification_budget_invalid")


@dataclass(frozen=True, slots=True)
class VerificationAssignmentV1:
    evidence_assignment: Mapping[str, Any]
    repository_revision: str
    profile_id: str
    profile_digest: str
    toolchain_digest: str
    backend: str
    target_symbols: Sequence[str]
    budgets: VerificationBudgets
    schema: str = VERIFICATION_ASSIGNMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != VERIFICATION_ASSIGNMENT_SCHEMA:
            raise ValueError("verification_assignment_schema_invalid")
        evidence = validate_hub_evidence_assignment(self.evidence_assignment)
        if not str(evidence["run_id"]).startswith("RUN_"):
            raise ValueError("verification_run_identity_invalid")
        _require_digest(self.repository_revision, "verification_repository_revision_invalid")
        _require_digest(self.profile_digest, "verification_profile_digest_invalid")
        _require_digest(self.toolchain_digest, "verification_toolchain_digest_invalid")
        _require_id(self.profile_id, "verification_profile_id_invalid")
        if self.backend not in {
            "hypothesis",
            "crosshair_check",
            "crosshair_cover",
            "crosshair_backend",
            "crosshair_diff",
        }:
            raise ValueError("verification_backend_invalid")
        targets = tuple(str(item).strip() for item in self.target_symbols)
        if not targets or len(targets) != len(set(targets)) or any(not item or len(item) > 256 for item in targets):
            raise ValueError("verification_targets_invalid")
        budgets = (
            self.budgets if isinstance(self.budgets, VerificationBudgets) else VerificationBudgets(**dict(self.budgets))
        )
        if len(targets) > budgets.max_targets:
            raise ValueError("verification_target_budget_exceeded")
        object.__setattr__(self, "evidence_assignment", evidence)
        object.__setattr__(self, "target_symbols", targets)
        object.__setattr__(self, "budgets", budgets)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["target_symbols"] = list(self.target_symbols)
        return result

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VerificationAssignmentV1":
        _closed_mapping(value, set(cls.__dataclass_fields__), "verification_assignment_fields_invalid")
        raw = dict(value)
        raw["budgets"] = VerificationBudgets(**dict(raw["budgets"]))
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class CounterexampleV1:
    run_ref: str
    property_ref: str
    target_symbol: str
    concrete_arguments: Any
    observed_result: Any
    expected_invariant: str
    reproduction_command: Sequence[str]
    test_candidate_digest: str
    schema: str = COUNTEREXAMPLE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != COUNTEREXAMPLE_SCHEMA or not str(self.run_ref).startswith("RUN_"):
            raise ValueError("verification_counterexample_identity_invalid")
        _require_id(self.property_ref, "verification_property_ref_invalid")
        if not self.target_symbol or len(self.target_symbol) > 256:
            raise ValueError("verification_target_symbol_invalid")
        arguments = _json_concrete(self.concrete_arguments)
        observed = _json_concrete(self.observed_result)
        command = tuple(str(item) for item in self.reproduction_command)
        if not command or len(command) > 16 or any(not item or len(item) > 512 for item in command):
            raise ValueError("verification_reproduction_command_invalid")
        _require_digest(self.test_candidate_digest, "verification_test_candidate_digest_invalid")
        if not self.expected_invariant or len(self.expected_invariant) > 2_000:
            raise ValueError("verification_expected_invariant_invalid")
        object.__setattr__(self, "concrete_arguments", arguments)
        object.__setattr__(self, "observed_result", observed)
        object.__setattr__(self, "reproduction_command", command)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reproduction_command"] = list(self.reproduction_command)
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CounterexampleV1":
        _closed_mapping(value, set(cls.__dataclass_fields__), "verification_counterexample_fields_invalid")
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class VerificationReportV1:
    assignment_digest: str
    run_ref: str
    repository_revision: str
    profile_id: str
    profile_digest: str
    toolchain_digest: str
    backend: str
    target_symbols: Sequence[str]
    status: VerificationStatus
    reason_code: str
    cases_executed: int
    duration_ms: int
    output_digest: str
    counterexamples: Sequence[Mapping[str, Any]] = ()
    schema: str = VERIFICATION_REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != VERIFICATION_REPORT_SCHEMA or not str(self.run_ref).startswith("RUN_"):
            raise ValueError("verification_report_identity_invalid")
        for value, reason in (
            (self.assignment_digest, "verification_assignment_digest_invalid"),
            (self.repository_revision, "verification_repository_revision_invalid"),
            (self.profile_digest, "verification_profile_digest_invalid"),
            (self.toolchain_digest, "verification_toolchain_digest_invalid"),
            (self.output_digest, "verification_output_digest_invalid"),
        ):
            _require_digest(value, reason)
        _require_id(self.profile_id, "verification_profile_id_invalid")
        _require_id(self.reason_code, "verification_reason_code_invalid")
        try:
            status = VerificationStatus(self.status)
        except ValueError as exc:
            raise ValueError("verification_report_status_invalid") from exc
        if type(self.cases_executed) is not int or self.cases_executed < 0:
            raise ValueError("verification_cases_invalid")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("verification_duration_invalid")
        targets = tuple(str(item) for item in self.target_symbols)
        if not targets:
            raise ValueError("verification_targets_invalid")
        counterexamples = tuple(CounterexampleV1.from_mapping(item).to_dict() for item in self.counterexamples)
        if status is VerificationStatus.COUNTEREXAMPLE_FOUND and not counterexamples:
            raise ValueError("verification_counterexample_required")
        if status is not VerificationStatus.COUNTEREXAMPLE_FOUND and counterexamples:
            raise ValueError("verification_counterexample_status_mismatch")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "target_symbols", targets)
        object.__setattr__(self, "counterexamples", counterexamples)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = VerificationStatus(self.status).value
        value["target_symbols"] = list(self.target_symbols)
        value["counterexamples"] = [dict(item) for item in self.counterexamples]
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VerificationReportV1":
        _closed_mapping(value, set(cls.__dataclass_fields__), "verification_report_fields_invalid")
        raw = dict(value)
        raw["status"] = VerificationStatus(raw["status"])
        return cls(**raw)


__all__ = [
    "COUNTEREXAMPLE_SCHEMA",
    "VERIFICATION_ASSIGNMENT_SCHEMA",
    "VERIFICATION_REPORT_SCHEMA",
    "CounterexampleV1",
    "VerificationAssignmentV1",
    "VerificationBudgets",
    "VerificationReportV1",
    "VerificationStatus",
    "canonical_digest",
    "counterexample_candidate_digest",
]
