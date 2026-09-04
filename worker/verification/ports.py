"""Small tool-neutral ports implemented only on the Worker side."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.verification import VerificationAssignmentV1, VerificationReportV1


class PropertyTestRunnerPort(Protocol):
    def run(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1: ...


class SymbolicContractCheckerPort(Protocol):
    def check(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1: ...


class BehaviorDiffRunnerPort(Protocol):
    def compare(
        self,
        assignment: VerificationAssignmentV1,
        *,
        repository: Path,
        left_symbol: str,
        right_symbol: str,
    ) -> VerificationReportV1: ...


class CounterexampleMaterializerPort(Protocol):
    def materialize(self, raw: Mapping[str, Any], *, reproduction_command: Sequence[str]) -> dict[str, Any]: ...


class VerificationArtifactPublisherPort(Protocol):
    def publish(self, report: VerificationReportV1, *, workspace: Path) -> Mapping[str, Any]: ...


__all__ = [
    "BehaviorDiffRunnerPort",
    "CounterexampleMaterializerPort",
    "PropertyTestRunnerPort",
    "SymbolicContractCheckerPort",
    "VerificationArtifactPublisherPort",
]
