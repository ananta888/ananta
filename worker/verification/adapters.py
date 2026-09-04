"""Optional Hypothesis and CrossHair command adapters.

The module intentionally imports neither tool.  Executables live only in the
isolated Worker image, while reports expose tool-neutral contracts.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

from ananta_contracts.verification import (
    VerificationAssignmentV1,
    VerificationReportV1,
    VerificationStatus,
)
from worker.verification.materializer import JsonCounterexampleMaterializer
from worker.verification.process_runner import ProcessObservation, VerificationProcessRunner

_COUNTEREXAMPLE = re.compile(
    r"(?P<message>.+?) when calling (?P<symbol>[A-Za-z0-9_.]+)\((?P<arguments>.*?)\)(?: \(|$)",
    re.MULTILINE,
)


class _AdapterBase:
    def __init__(self, process_runner: VerificationProcessRunner | None = None) -> None:
        self._process_runner = process_runner or VerificationProcessRunner()

    def _execute(
        self,
        assignment: VerificationAssignmentV1,
        *,
        repository: Path,
        command: Sequence[str],
        extra_env: dict[str, str] | None = None,
    ) -> ProcessObservation:
        return self._process_runner.run(
            command,
            repository=repository,
            timeout_seconds=assignment.budgets.timeout_seconds,
            max_output_bytes=assignment.budgets.max_output_bytes,
            memory_mb=assignment.budgets.memory_mb,
            extra_env=extra_env,
        )

    @staticmethod
    def _report(
        assignment: VerificationAssignmentV1,
        observation: ProcessObservation,
        *,
        status: VerificationStatus,
        reason_code: str,
        cases_executed: int = 0,
        counterexamples: Sequence[dict] = (),
    ) -> VerificationReportV1:
        output = (observation.stdout + "\n" + observation.stderr).encode()
        return VerificationReportV1(
            assignment_digest=assignment.digest,
            run_ref=str(assignment.evidence_assignment["run_id"]),
            repository_revision=assignment.repository_revision,
            profile_id=assignment.profile_id,
            profile_digest=assignment.profile_digest,
            toolchain_digest=assignment.toolchain_digest,
            backend=assignment.backend,
            target_symbols=assignment.target_symbols,
            status=status,
            reason_code=reason_code,
            cases_executed=cases_executed,
            duration_ms=observation.duration_ms,
            output_digest=hashlib.sha256(output).hexdigest(),
            counterexamples=counterexamples,
        )


class PytestHypothesisRunnerAdapter(_AdapterBase):
    @staticmethod
    def _pytest_command(repository: Path, target_symbols: Sequence[str]) -> list[str]:
        verification_root = repository / "tests" / "verification"
        return [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--confcutdir={verification_root}",
            *target_symbols,
        ]

    def run(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        command = self._pytest_command(repository, assignment.target_symbols)
        observation = self._execute(
            assignment,
            repository=repository,
            command=command,
            extra_env={"ANANTA_HYPOTHESIS_CASES": str(assignment.budgets.max_cases)},
        )
        if observation.timed_out:
            return self._report(
                assignment, observation, status=VerificationStatus.TIMED_OUT, reason_code="budget_timeout"
            )
        if observation.output_truncated:
            return self._report(
                assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="output_budget_exceeded"
            )
        if observation.returncode == 0:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.PASSED,
                reason_code="properties_satisfied",
                cases_executed=assignment.budgets.max_cases,
            )
        return self._report(assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="pytest_failed")


class HypothesisCrossHairBackendAdapter(PytestHypothesisRunnerAdapter):
    def run(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        command = self._pytest_command(repository, assignment.target_symbols)
        observation = self._execute(
            assignment,
            repository=repository,
            command=command,
            extra_env={
                "ANANTA_HYPOTHESIS_BACKEND": "crosshair",
                "ANANTA_HYPOTHESIS_CASES": str(assignment.budgets.max_cases),
            },
        )
        if observation.timed_out:
            return self._report(
                assignment, observation, status=VerificationStatus.TIMED_OUT, reason_code="budget_timeout"
            )
        if observation.returncode == 0 and not observation.output_truncated:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.PASSED_WITH_BOUNDED_SEARCH,
                reason_code="crosshair_backend_bounded_search_complete",
                cases_executed=assignment.budgets.max_cases,
            )
        reason = "output_budget_exceeded" if observation.output_truncated else "crosshair_backend_failed"
        return self._report(assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code=reason)


class CrossHairCheckAdapter(_AdapterBase):
    def check(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        per_condition_timeout = min(
            2,
            max(1, assignment.budgets.timeout_seconds // len(assignment.target_symbols)),
        )
        command = [
            sys.executable,
            "-m",
            "crosshair",
            "check",
            "--analysis_kind=asserts,PEP316",
            f"--per_condition_timeout={per_condition_timeout}",
            "--report_all",
            *assignment.target_symbols,
        ]
        observation = self._execute(assignment, repository=repository, command=command)
        return self._classify(assignment, observation)

    def _classify(self, assignment: VerificationAssignmentV1, observation: ProcessObservation) -> VerificationReportV1:
        if observation.timed_out:
            return self._report(
                assignment, observation, status=VerificationStatus.TIMED_OUT, reason_code="budget_timeout"
            )
        if observation.output_truncated:
            return self._report(
                assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="output_budget_exceeded"
            )
        counterexample = self._parse_counterexample(assignment, observation.stdout + "\n" + observation.stderr)
        if counterexample:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.COUNTEREXAMPLE_FOUND,
                reason_code="contract_counterexample_found",
                counterexamples=[counterexample],
            )
        if observation.returncode == 0:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.PASSED_WITH_BOUNDED_SEARCH,
                reason_code="bounded_search_no_counterexample",
            )
        return self._report(
            assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="crosshair_check_failed"
        )

    @staticmethod
    def _parse_counterexample(assignment: VerificationAssignmentV1, output: str) -> dict | None:
        match = _COUNTEREXAMPLE.search(output)
        if match is None:
            return None
        arguments: dict[str, object] = {}
        raw_arguments = match.group("arguments")
        for item in raw_arguments.split(","):
            name, separator, value = item.partition("=")
            if not separator:
                continue
            try:
                arguments[name.strip()] = ast.literal_eval(value.strip())
            except (SyntaxError, ValueError):
                arguments[name.strip()] = value.strip()
        if not arguments and raw_arguments.strip():
            try:
                positional = ast.literal_eval(f"({raw_arguments},)")
                arguments["args"] = list(positional)
            except (SyntaxError, ValueError):
                arguments["args"] = [raw_arguments.strip()]
        raw = {
            "run_ref": assignment.evidence_assignment["run_id"],
            "property_ref": "crosshair-contract",
            "target_symbol": match.group("symbol"),
            "concrete_arguments": arguments,
            "observed_result": {"message": match.group("message").strip()},
            "expected_invariant": "declared contract must hold",
            "schema": "ananta.counterexample.v1",
        }
        return JsonCounterexampleMaterializer().materialize(
            raw,
            reproduction_command=["pytest", "-q", "tests/verification/test_crosshair_pilot.py"],
        )


class CrossHairCoverAdapter(_AdapterBase):
    def check(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        command = [
            sys.executable,
            "-m",
            "crosshair",
            "cover",
            "--example_output_format=pytest",
            f"--per_path_timeout={max(1, assignment.budgets.timeout_seconds // len(assignment.target_symbols))}",
            *assignment.target_symbols,
        ]
        observation = self._execute(assignment, repository=repository, command=command)
        if observation.timed_out:
            return self._report(
                assignment, observation, status=VerificationStatus.TIMED_OUT, reason_code="budget_timeout"
            )
        if observation.returncode == 0 and observation.stdout.strip():
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.PASSED_WITH_BOUNDED_SEARCH,
                reason_code="cover_candidates_generated",
            )
        status = VerificationStatus.INCONCLUSIVE if observation.returncode == 0 else VerificationStatus.TOOL_ERROR
        reason = "cover_no_candidates" if observation.returncode == 0 else "crosshair_cover_failed"
        return self._report(assignment, observation, status=status, reason_code=reason)


class CrossHairDiffBehaviorAdapter(_AdapterBase):
    def compare(
        self,
        assignment: VerificationAssignmentV1,
        *,
        repository: Path,
        left_symbol: str,
        right_symbol: str,
    ) -> VerificationReportV1:
        if left_symbol not in assignment.target_symbols or right_symbol not in assignment.target_symbols:
            raise ValueError("verification_diff_target_outside_assignment")
        observation = self._execute(
            assignment,
            repository=repository,
            command=[
                sys.executable,
                "-m",
                "crosshair",
                "diffbehavior",
                f"--per_path_timeout={max(1, assignment.budgets.timeout_seconds // 2)}",
                left_symbol,
                right_symbol,
            ],
        )
        if observation.timed_out:
            status, reason = VerificationStatus.TIMED_OUT, "budget_timeout"
        elif "Given:" in observation.stdout:
            status, reason = VerificationStatus.PASSED_WITH_BOUNDED_SEARCH, "behavior_difference_observed"
        elif observation.returncode == 0 and "No differences found" in observation.stdout:
            status, reason = VerificationStatus.INCONCLUSIVE, "bounded_diff_no_difference"
        elif observation.returncode != 0:
            status, reason = VerificationStatus.TOOL_ERROR, "crosshair_diff_failed"
        else:
            status, reason = VerificationStatus.INCONCLUSIVE, "bounded_diff_no_difference"
        return self._report(assignment, observation, status=status, reason_code=reason)


class FakeVerificationRunnerAdapter:
    """Deterministic test double; never creates production evidence."""

    def __init__(self, factory: Callable[[VerificationAssignmentV1], VerificationReportV1]) -> None:
        self._factory = factory

    def run(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        if assignment.evidence_assignment["evidence_scope"] not in {"test", "local"}:
            raise ValueError("verification_fake_production_evidence_denied")
        return self._factory(assignment)


__all__ = [
    "CrossHairCheckAdapter",
    "CrossHairCoverAdapter",
    "CrossHairDiffBehaviorAdapter",
    "FakeVerificationRunnerAdapter",
    "HypothesisCrossHairBackendAdapter",
    "PytestHypothesisRunnerAdapter",
]
