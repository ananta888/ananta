"""Optional Hypothesis and CrossHair command adapters.

The module intentionally imports neither tool.  Executables live only in the
isolated Worker image, while reports expose tool-neutral contracts.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Callable, Sequence

from ananta_contracts.verification import (
    VerificationAssignmentV1,
    VerificationReportV1,
    VerificationStatus,
)
from worker.verification.crosshair_output import CrossHairOutputParseError, CrossHairOutputParser
from worker.verification.materializer import JsonCounterexampleMaterializer
from worker.verification.process_runner import ProcessObservation, VerificationProcessRunner
from worker.verification.pytest_results import PytestRunSummary


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
        collected_tests: int = 0,
        passed_tests: int = 0,
        failed_tests: int = 0,
        bounded_search_metadata: dict[str, object] | None = None,
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
            collected_tests=collected_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            bounded_search_metadata=bounded_search_metadata or {},
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
            "-p",
            "worker.verification.pytest_result_plugin",
            f"--confcutdir={verification_root}",
            *target_symbols,
        ]

    @staticmethod
    def _property_counterexamples(
        assignment: VerificationAssignmentV1,
        summary: PytestRunSummary,
    ) -> list[dict]:
        return [
            JsonCounterexampleMaterializer().materialize(
                {
                    "run_ref": assignment.evidence_assignment["run_id"],
                    "property_ref": f"pytest-property-{index}",
                    "target_symbol": node_id,
                    "concrete_arguments": {"pytest_node_id": node_id},
                    "observed_result": {"outcome": "failed"},
                    "expected_invariant": "property must hold for generated examples",
                    "schema": "ananta.counterexample.v1",
                },
                reproduction_command=[
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--confcutdir=tests/verification",
                    node_id,
                ],
            )
            for index, node_id in enumerate(summary.failed_node_ids, start=1)
        ]

    def _classify_pytest(
        self,
        assignment: VerificationAssignmentV1,
        observation: ProcessObservation,
        *,
        success_status: VerificationStatus,
        success_reason: str,
    ) -> VerificationReportV1:
        if observation.timed_out:
            return self._report(
                assignment, observation, status=VerificationStatus.TIMED_OUT, reason_code="budget_timeout"
            )
        if observation.output_truncated:
            return self._report(
                assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="output_budget_exceeded"
            )
        summary = PytestRunSummary.from_output(observation.stdout + "\n" + observation.stderr)
        if summary is None:
            return self._report(
                assignment, observation, status=VerificationStatus.TOOL_ERROR, reason_code="pytest_result_missing"
            )
        common = {
            "collected_tests": summary.collected,
            "passed_tests": summary.passed,
            "failed_tests": summary.failed,
            "bounded_search_metadata": {
                **summary.metadata(),
                "configured_max_cases": assignment.budgets.max_cases,
                "case_count_observed": False,
                "result_source": "internal_pytest_plugin",
            },
        }
        if summary.errors:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.TOOL_ERROR,
                reason_code="pytest_collection_or_environment_failed",
                **common,
            )
        if summary.failed:
            counterexamples = self._property_counterexamples(assignment, summary)
            if len(counterexamples) != summary.failed:
                return self._report(
                    assignment,
                    observation,
                    status=VerificationStatus.UNSUPPORTED,
                    reason_code="pytest_counterexample_materialization_failed",
                    **common,
                )
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.COUNTEREXAMPLE_FOUND,
                reason_code="property_counterexample_found",
                counterexamples=counterexamples,
                **common,
            )
        if observation.returncode == 0:
            return self._report(
                assignment,
                observation,
                status=success_status,
                reason_code=success_reason,
                **common,
            )
        return self._report(
            assignment,
            observation,
            status=VerificationStatus.TOOL_ERROR,
            reason_code="pytest_tool_failed",
            **common,
        )

    def run(self, assignment: VerificationAssignmentV1, *, repository: Path) -> VerificationReportV1:
        command = self._pytest_command(repository, assignment.target_symbols)
        observation = self._execute(
            assignment,
            repository=repository,
            command=command,
            extra_env={"ANANTA_HYPOTHESIS_CASES": str(assignment.budgets.max_cases)},
        )
        return self._classify_pytest(
            assignment,
            observation,
            success_status=VerificationStatus.PASSED,
            success_reason="properties_satisfied",
        )


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
        return self._classify_pytest(
            assignment,
            observation,
            success_status=VerificationStatus.PASSED_WITH_BOUNDED_SEARCH,
            success_reason="crosshair_backend_bounded_search_complete",
        )


class CrossHairCheckAdapter(_AdapterBase):
    def __init__(
        self,
        process_runner: VerificationProcessRunner | None = None,
        output_parser: CrossHairOutputParser | None = None,
    ) -> None:
        super().__init__(process_runner)
        self._output_parser = output_parser or CrossHairOutputParser()

    @staticmethod
    def _assigned_target(assignment: VerificationAssignmentV1, parsed_symbol: str) -> str:
        matches = [
            target
            for target in assignment.target_symbols
            if target == parsed_symbol or target.endswith(f".{parsed_symbol}")
        ]
        if len(matches) != 1:
            raise CrossHairOutputParseError("crosshair_counterexample_target_ambiguous")
        return matches[0]

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
        output = observation.stdout + "\n" + observation.stderr
        try:
            parsed = self._output_parser.parse(output)
            counterexamples = []
            for index, item in enumerate(parsed, start=1):
                assigned_target = self._assigned_target(assignment, item.symbol)
                counterexamples.append(
                    JsonCounterexampleMaterializer().materialize(
                        {
                            "run_ref": assignment.evidence_assignment["run_id"],
                            "property_ref": f"crosshair-contract-{index}",
                            "target_symbol": assigned_target,
                            "concrete_arguments": item.arguments,
                            "observed_result": {"message": item.message},
                            "expected_invariant": "declared contract must hold",
                            "schema": "ananta.counterexample.v1",
                        },
                        reproduction_command=[
                            "python",
                            "-m",
                            "crosshair",
                            "check",
                            "--analysis_kind=asserts,PEP316",
                            assigned_target,
                        ],
                    )
                )
        except (CrossHairOutputParseError, ValueError):
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.UNSUPPORTED,
                reason_code="counterexample_parse_unsupported",
            )
        if counterexamples:
            return self._report(
                assignment,
                observation,
                status=VerificationStatus.COUNTEREXAMPLE_FOUND,
                reason_code="contract_counterexample_found",
                counterexamples=counterexamples,
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
