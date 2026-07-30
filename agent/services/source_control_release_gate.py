"""Fail-closed source-control release-gate aggregation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional


REQUIRED_SOURCE_CONTROL_GATES = (
    "contract",
    "security",
    "migration",
    "backend",
    "angular",
    "e2e",
    "accessibility",
    "container",
    "no_bypass",
    "rollout",
    "load_recovery",
)
_FINAL_STATUSES = frozenset({"passed", "failed", "unverified"})
_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_RUN_ID = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class SourceControlReleaseGateError(ValueError):
    pass


@dataclass(frozen=True)
class GateEvidence:
    gate: str
    status: str
    artifact_digest: str

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "GateEvidence":
        if set(payload) != {"gate", "status", "artifact_digest"}:
            raise SourceControlReleaseGateError(
                "gate evidence must contain only gate, status, and artifact_digest"
            )
        gate = str(payload["gate"])
        status = str(payload["status"])
        artifact_digest = str(payload["artifact_digest"])
        if gate not in REQUIRED_SOURCE_CONTROL_GATES:
            raise SourceControlReleaseGateError(f"unknown gate: {gate}")
        if status not in _FINAL_STATUSES:
            raise SourceControlReleaseGateError(f"unknown gate status: {status}")
        if len(artifact_digest) != 64 or any(
            char not in "0123456789abcdef" for char in artifact_digest
        ):
            raise SourceControlReleaseGateError(
                "artifact_digest must be a lowercase SHA-256 digest"
            )
        return cls(gate=gate, status=status, artifact_digest=artifact_digest)


@dataclass(frozen=True)
class ProductionVerification:
    status: str
    source_id: Optional[str]
    run_id: Optional[str]
    artifact_digest: Optional[str]

    @classmethod
    def unverified(cls) -> "ProductionVerification":
        return cls(
            status="unverified",
            source_id=None,
            run_id=None,
            artifact_digest=None,
        )

    @classmethod
    def parse(
        cls,
        payload: Optional[Mapping[str, Any]],
    ) -> "ProductionVerification":
        if payload is None:
            return cls.unverified()
        if set(payload) != {
            "status",
            "source_id",
            "run_id",
            "artifact_digest",
        }:
            raise SourceControlReleaseGateError(
                "production verification has an invalid shape"
            )
        status = str(payload["status"])
        source_id = payload["source_id"]
        run_id = payload["run_id"]
        artifact_digest = payload["artifact_digest"]
        if status != "passed":
            raise SourceControlReleaseGateError(
                "provided production verification must be passed"
            )
        if not all(
            isinstance(value, str) and value.strip()
            for value in (source_id, run_id, artifact_digest)
        ):
            raise SourceControlReleaseGateError(
                "passed production verification requires supplied source/run IDs"
            )
        if not _SOURCE_ID.fullmatch(source_id) or not _RUN_ID.fullmatch(run_id):
            raise SourceControlReleaseGateError(
                "production verification requires provided valid SRC_*/RUN_* IDs"
            )
        if len(artifact_digest) != 64 or any(
            char not in "0123456789abcdef" for char in artifact_digest
        ):
            raise SourceControlReleaseGateError(
                "production artifact_digest must be a lowercase SHA-256 digest"
            )
        return cls(
            status=status,
            source_id=source_id,
            run_id=run_id,
            artifact_digest=artifact_digest,
        )


@dataclass(frozen=True)
class SourceControlReleaseGateReport:
    release_allowed: bool
    gates: Mapping[str, str]
    missing_gates: tuple[str, ...]
    production_verification: ProductionVerification

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "release_allowed": self.release_allowed,
            "gates": dict(self.gates),
            "missing_gates": list(self.missing_gates),
            "production_verification": {
                "status": self.production_verification.status,
                "source_id": self.production_verification.source_id,
                "run_id": self.production_verification.run_id,
                "artifact_digest": self.production_verification.artifact_digest,
            },
        }


def evaluate_source_control_release_gate(
    evidence_payloads: Iterable[Mapping[str, Any]],
    *,
    production_payload: Optional[Mapping[str, Any]] = None,
) -> SourceControlReleaseGateReport:
    evidence_by_gate: dict[str, GateEvidence] = {}
    for payload in evidence_payloads:
        evidence = GateEvidence.parse(payload)
        if evidence.gate in evidence_by_gate:
            raise SourceControlReleaseGateError(
                f"duplicate evidence for gate: {evidence.gate}"
            )
        evidence_by_gate[evidence.gate] = evidence
    missing = tuple(
        gate
        for gate in REQUIRED_SOURCE_CONTROL_GATES
        if gate not in evidence_by_gate
    )
    production = ProductionVerification.parse(production_payload)
    gates = {
        gate: evidence_by_gate[gate].status
        if gate in evidence_by_gate
        else "unverified"
        for gate in REQUIRED_SOURCE_CONTROL_GATES
    }
    release_allowed = (
        not missing
        and all(status == "passed" for status in gates.values())
        and production.status == "passed"
    )
    return SourceControlReleaseGateReport(
        release_allowed=release_allowed,
        gates=gates,
        missing_gates=missing,
        production_verification=production,
    )
