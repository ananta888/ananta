"""Versioned training examples from gate and benchmark runs (GBMF-002).

Every admitted example carries ``provenance``, ``contract_version`` and
``label_source``. Admission is fail-closed: secrets/PII are redacted or the
example is blocked, duplicates collapse to one weight, and contradictory
deterministic labels for the same input are quarantined instead of trained on.

The store is a small append-only in-memory ledger behind a port so the Hub can
persist it wherever it persists datasets; it deliberately owns no training.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import (
    LABEL_SOURCES,
    REPRODUCIBLE_LABEL_SOURCES,
    SpecialistContractError,
    SpecialistDecisionContract,
    canonical_digest,
)

EXAMPLE_SCHEMA = "ananta.specialist-training-example.v1"
ORIGIN_KINDS = frozenset({"gate_run", "benchmark_case", "error_case", "counterexample", "human_review"})
ERROR_CLASSES = frozenset(
    {"none", "false_allow", "false_deny", "abstention_error", "parser_error", "schema_error", "counterexample"}
)
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$")


class SpecialistExampleError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class TextRedactor:
    """Small deterministic redactor; blocks what it cannot safely mask."""

    VERSION = "specialist-redaction-v1"
    _SECRET_PATTERNS = (
        re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
        re.compile(r"\b(?:AKIA|ASIA|AROA)[A-Z0-9]{16}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\bbearer\s+[A-Za-z0-9._-]{16,}\b", re.I),
        re.compile(r"\b(?:api[-_]?key|password|passwd|secret|token|credential)\s*[:=]\s*\S{4,}", re.I),
    )
    _PII_PATTERNS = (
        re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I),
        re.compile(r"(?<!\w)\+?\d[\d ()/.-]{8,}\d(?!\w)"),
    )
    BLOCKING = (_SECRET_PATTERNS[0],)  # private keys are never masked into training data

    def redact(self, text: str) -> tuple[str, list[str]]:
        findings: list[str] = []
        for pattern in self.BLOCKING:
            if pattern.search(text):
                raise SpecialistExampleError("specialist_example_blocked_secret")
        for pattern in self._SECRET_PATTERNS[1:]:
            text, count = pattern.subn("[REDACTED_SECRET]", text)
            if count:
                findings.append("secret")
        for pattern in self._PII_PATTERNS:
            text, count = pattern.subn("[REDACTED_PII]", text)
            if count:
                findings.append("pii")
        return text, findings

    def redact_value(self, value: Any) -> tuple[Any, list[str]]:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            items, findings = [], []
            for item in value:
                cleaned, found = self.redact_value(item)
                items.append(cleaned)
                findings.extend(found)
            return items, findings
        if isinstance(value, Mapping):
            result, findings = {}, []
            for key, child in value.items():
                cleaned, found = self.redact_value(child)
                result[str(key)] = cleaned
                findings.extend(found)
            return result, findings
        return value, []


@dataclass(frozen=True)
class Provenance:
    origin_kind: str
    origin_ref: str  # gate run id, benchmark case id, evidence RUN_* id ...
    captured_at: str
    gate_name: str = ""
    run_id: str | None = None  # Hub-issued RUN_* when available; never self-minted

    def __post_init__(self) -> None:
        if self.origin_kind not in ORIGIN_KINDS or not _REF.fullmatch(self.origin_ref):
            raise SpecialistExampleError("specialist_example_provenance_invalid")
        if not isinstance(self.captured_at, str) or not 4 <= len(self.captured_at) <= 40:
            raise SpecialistExampleError("specialist_example_provenance_invalid")
        if self.run_id is not None and not re.fullmatch(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$", self.run_id):
            raise SpecialistExampleError("specialist_example_provenance_invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "origin_kind": self.origin_kind,
            "origin_ref": self.origin_ref,
            "captured_at": self.captured_at,
            "gate_name": self.gate_name,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class TrainingExample:
    example_id: str  # content digest: identical inputs+labels collapse
    specialist_id: str
    contract_version: str
    input: dict[str, Any]
    label: str
    label_source: str
    provenance: Provenance
    group_key: str  # near-duplicate/variant group for leakage-safe splits
    input_digest: str
    model_label: str | None = None  # what the (previous) model decided, if any
    error_class: str = "none"
    redactions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": EXAMPLE_SCHEMA,
            "example_id": self.example_id,
            "specialist_id": self.specialist_id,
            "contract_version": self.contract_version,
            "input": self.input,
            "label": self.label,
            "label_source": self.label_source,
            "model_label": self.model_label,
            "error_class": self.error_class,
            "provenance": self.provenance.to_mapping(),
            "group_key": self.group_key,
            "input_digest": self.input_digest,
            "redactions": list(self.redactions),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class QuarantineRecord:
    example_id: str
    reason_code: str
    conflicting_example_id: str | None = None


@dataclass
class AdmissionReport:
    admitted: list[TrainingExample] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "admitted": len(self.admitted),
            "duplicates": len(self.duplicates),
            "quarantined": [record.__dict__ for record in self.quarantined],
            "rejected": [{"index": index, "reason_code": code} for index, code in self.rejected],
        }


def _normalized_text(value: Any) -> str:
    return " ".join(str(value).lower().split())


def default_group_key(contract: SpecialistDecisionContract, normalized_input: Mapping[str, Any]) -> str:
    """Group semantically near variants: same text content ignoring case/whitespace.

    Callers with a better notion of "same task" (e.g. a benchmark case id)
    pass ``group_key`` explicitly; this default only folds trivial variants.
    """
    parts = []
    for spec in contract.input_fields:
        value = normalized_input.get(spec.name)
        if spec.type == "string" and isinstance(value, str):
            parts.append(_normalized_text(value)[:512])
        elif spec.type == "string_list" and isinstance(value, list):
            parts.append(_normalized_text(" ".join(sorted(map(str, value))))[:512])
    return canonical_digest(parts if parts else normalized_input)


class SpecialistExampleAdmission:
    """Turns raw gate/benchmark observations into admitted, redacted examples."""

    def __init__(self, contract: SpecialistDecisionContract, *, redactor: TextRedactor | None = None) -> None:
        self.contract = contract
        self._redactor = redactor if redactor is not None else TextRedactor()

    def admit(self, raw: Mapping[str, Any]) -> TrainingExample:
        if not isinstance(raw, Mapping):
            raise SpecialistExampleError("specialist_example_invalid")
        if str(raw.get("specialist_id") or "") != self.contract.specialist_id:
            raise SpecialistExampleError("specialist_example_contract_mismatch")
        if str(raw.get("contract_version") or self.contract.contract_version) != self.contract.contract_version:
            raise SpecialistExampleError("specialist_example_contract_mismatch")
        label_source = str(raw.get("label_source") or "")
        if label_source not in LABEL_SOURCES:
            raise SpecialistExampleError("specialist_example_label_source_invalid")
        if label_source not in REPRODUCIBLE_LABEL_SOURCES and raw.get("reviewed") is not True:
            raise SpecialistExampleError("specialist_example_label_not_reproducible")
        try:
            normalized = self.contract.validate_input(raw.get("input"))
            label = self.contract.validate_label(raw.get("label"))
        except SpecialistContractError as exc:
            raise SpecialistExampleError(exc.reason_code) from exc
        model_label = raw.get("model_label")
        if model_label is not None:
            try:
                model_label = self.contract.validate_label(model_label)
            except SpecialistContractError as exc:
                raise SpecialistExampleError(exc.reason_code) from exc
        error_class = str(raw.get("error_class") or "none")
        if error_class not in ERROR_CLASSES:
            raise SpecialistExampleError("specialist_example_error_class_invalid")
        provenance_raw = raw.get("provenance")
        if not isinstance(provenance_raw, Mapping):
            raise SpecialistExampleError("specialist_example_provenance_invalid")
        provenance = Provenance(
            origin_kind=str(provenance_raw.get("origin_kind") or ""),
            origin_ref=str(provenance_raw.get("origin_ref") or ""),
            captured_at=str(provenance_raw.get("captured_at") or ""),
            gate_name=str(provenance_raw.get("gate_name") or "")[:128],
            run_id=provenance_raw.get("run_id"),
        )
        redacted, findings = self._redactor.redact_value(normalized)
        input_digest = canonical_digest(redacted)
        metadata = {str(k): v for k, v in dict(raw.get("metadata") or {}).items() if isinstance(v, (str, int, float, bool))}
        return TrainingExample(
            example_id=canonical_digest({"input": input_digest, "label": label, "contract": self.contract.contract_key}),
            specialist_id=self.contract.specialist_id,
            contract_version=self.contract.contract_version,
            input=redacted,
            label=label,
            label_source=label_source,
            provenance=provenance,
            group_key=str(raw.get("group_key") or default_group_key(self.contract, redacted)),
            input_digest=input_digest,
            model_label=model_label,
            error_class=error_class,
            redactions=tuple(sorted(set(findings))),
            metadata=metadata,
        )

    def admit_batch(self, raws: Iterable[Mapping[str, Any]], *, existing: Iterable[TrainingExample] = ()) -> AdmissionReport:
        """Admit, deduplicate and quarantine contradictory deterministic labels."""
        report = AdmissionReport()
        seen: dict[str, TrainingExample] = {example.example_id: example for example in existing}
        by_input: dict[str, TrainingExample] = {example.input_digest: example for example in seen.values()}
        for index, raw in enumerate(raws):
            try:
                example = self.admit(raw)
            except SpecialistExampleError as exc:
                report.rejected.append((index, exc.reason_code))
                continue
            if example.example_id in seen:
                report.duplicates.append(example.example_id)
                continue
            other = by_input.get(example.input_digest)
            if other is not None and other.label != example.label:
                # Same input, different label. A deterministic source contradicting
                # itself is a labelling bug: quarantine both sides of the conflict.
                if example.label_source in REPRODUCIBLE_LABEL_SOURCES and other.label_source in REPRODUCIBLE_LABEL_SOURCES:
                    report.quarantined.append(QuarantineRecord(example.example_id, "contradictory_deterministic_label", other.example_id))
                    if other in report.admitted:
                        report.admitted.remove(other)
                        report.quarantined.append(QuarantineRecord(other.example_id, "contradictory_deterministic_label", example.example_id))
                    continue
                report.quarantined.append(QuarantineRecord(example.example_id, "label_conflict_requires_review", other.example_id))
                continue
            seen[example.example_id] = example
            by_input[example.input_digest] = example
            report.admitted.append(example)
        return report


def dataset_digest(examples: Iterable[TrainingExample]) -> str:
    """Order-independent digest over admitted example identities."""
    return canonical_digest(sorted(example.example_id for example in examples))
