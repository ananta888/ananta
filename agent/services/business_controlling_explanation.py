"""Deterministic, evidence-bounded explanations for controlling findings."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ananta_contracts.business_controlling import (
    BusinessFinding,
    FindingDisposition,
    FindingKind,
    derive_stable_id,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,190}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class BusinessControllingExplanationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class EvidenceStatementKind(str, Enum):
    RULE = "rule"
    CALCULATION = "calculation"
    STATISTICAL_RECEIPT = "statistical_receipt"


@dataclass(frozen=True)
class EvidenceStatement:
    kind: EvidenceStatementKind
    source_id: str
    evidence_digest: str
    bounded_text: str

    def validate(self) -> None:
        if (
            not isinstance(self.kind, EvidenceStatementKind)
            or _IDENTIFIER.fullmatch(self.source_id) is None
            or _DIGEST.fullmatch(self.evidence_digest) is None
            or not isinstance(self.bounded_text, str)
            or not self.bounded_text.strip()
            or len(self.bounded_text) > 500
            or any(ord(character) < 32 and character not in "\t\n" for character in self.bounded_text)
        ):
            raise BusinessControllingExplanationError(
                "controlling_explanation_evidence_invalid"
            )


@dataclass(frozen=True)
class EvidenceBoundedExplanation:
    schema_version: str
    explanation_id: str
    finding_id: str
    finding_kind: str
    calculation_statements: tuple[str, ...]
    anomaly_hypotheses: tuple[str, ...]
    requested_checks: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    permitted_dispositions: tuple[str, ...]
    explanation_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "explanation_id": self.explanation_id,
            "finding_id": self.finding_id,
            "finding_kind": self.finding_kind,
            "calculation_statements": list(self.calculation_statements),
            "anomaly_hypotheses": list(self.anomaly_hypotheses),
            "requested_checks": list(self.requested_checks),
            "evidence_refs": list(self.evidence_refs),
            "permitted_dispositions": list(self.permitted_dispositions),
            "explanation_digest": self.explanation_digest,
        }


class BusinessControllingExplanationService:
    """Render recorded evidence literally; never execute or reinterpret it."""

    def explain(
        self,
        *,
        finding: BusinessFinding,
        statements: Sequence[EvidenceStatement],
    ) -> EvidenceBoundedExplanation:
        bounded = tuple(statements)
        if not bounded or len(bounded) > 50:
            raise BusinessControllingExplanationError(
                "controlling_explanation_evidence_required"
            )
        for statement in bounded:
            statement.validate()
        if not any(
            statement.evidence_digest
            in {finding.evidence_digest, finding.execution_receipt_digest}
            for statement in bounded
        ):
            raise BusinessControllingExplanationError(
                "controlling_explanation_finding_binding_missing"
            )
        required_kind = {
            FindingKind.DETERMINISTIC_VIOLATION: EvidenceStatementKind.RULE,
            FindingKind.RECONCILIATION_MISMATCH: EvidenceStatementKind.CALCULATION,
            FindingKind.STATISTICAL_ANOMALY: EvidenceStatementKind.STATISTICAL_RECEIPT,
            FindingKind.ADVISORY_EXPLANATION: EvidenceStatementKind.CALCULATION,
        }[finding.kind]
        if not any(statement.kind is required_kind for statement in bounded):
            raise BusinessControllingExplanationError(
                "controlling_explanation_required_evidence_missing"
            )

        calculation = tuple(
            _literal(statement)
            for statement in bounded
            if statement.kind in {
                EvidenceStatementKind.RULE,
                EvidenceStatementKind.CALCULATION,
            }
        )
        hypotheses = tuple(
            _literal(statement)
            for statement in bounded
            if statement.kind is EvidenceStatementKind.STATISTICAL_RECEIPT
        )
        checks = _requested_checks(finding.kind)
        evidence_refs = tuple(
            f"{statement.kind.value}:{statement.source_id}:{statement.evidence_digest}"
            for statement in bounded
        )
        material = {
            "finding_id": finding.finding_id,
            "finding_kind": finding.kind.value,
            "calculation_statements": calculation,
            "anomaly_hypotheses": hypotheses,
            "requested_checks": checks,
            "evidence_refs": evidence_refs,
        }
        explanation_digest = _digest(material)
        return EvidenceBoundedExplanation(
            schema_version="ananta.business-controlling-explanation.v1",
            explanation_id=derive_stable_id("ctrlexplanation", material),
            finding_id=finding.finding_id,
            finding_kind=finding.kind.value,
            calculation_statements=calculation,
            anomaly_hypotheses=hypotheses,
            requested_checks=checks,
            evidence_refs=evidence_refs,
            permitted_dispositions=tuple(
                disposition.value
                for disposition in FindingDisposition
                if disposition is not FindingDisposition.OPEN
            ),
            explanation_digest=explanation_digest,
        )


def _literal(statement: EvidenceStatement) -> str:
    # The fixed prefix makes untrusted business text data, not instructions.
    collapsed = " ".join(statement.bounded_text.split())
    return f"[{statement.kind.value} data; {statement.source_id}] {collapsed}"


def _requested_checks(kind: FindingKind) -> tuple[str, ...]:
    return {
        FindingKind.DETERMINISTIC_VIOLATION: (
            "Verify the mapped field and the approved rule parameters.",
        ),
        FindingKind.RECONCILIATION_MISMATCH: (
            "Verify aggregation grain, exclusions, currency and tolerance.",
        ),
        FindingKind.STATISTICAL_ANOMALY: (
            "Check whether the event is seasonal, one-off or missing context.",
        ),
        FindingKind.ADVISORY_EXPLANATION: (
            "Confirm the cited evidence before recording a disposition.",
        ),
    }[kind]


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingExplanationError",
    "BusinessControllingExplanationService",
    "EvidenceBoundedExplanation",
    "EvidenceStatement",
    "EvidenceStatementKind",
]
