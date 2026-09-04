from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.verification_metrics_service import VerificationMetricsService
from agent.services.verification_promotion_service import VerificationPromotionService
from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from tests.verification.helpers import assignment
from worker.verification.adapters import (
    CrossHairCheckAdapter,
    CrossHairDiffBehaviorAdapter,
    FakeVerificationRunnerAdapter,
)
from worker.verification.materializer import JsonCounterexampleMaterializer
from worker.verification.process_runner import ProcessObservation, VerificationProcessRunner
from worker.verification.reproducer import CounterexampleReproducer
from worker.verification.revision_diff import RevisionBoundDiffRunner, RevisionPair


class StubProcessRunner:
    def __init__(self, observation: ProcessObservation) -> None:
        self.observation = observation
        self.commands: list[tuple[str, ...]] = []

    def run(self, command, **kwargs):
        self.commands.append(tuple(command))
        return self.observation


def test_crosshair_output_becomes_concrete_reproducible_counterexample(tmp_path: Path) -> None:
    observation = ProcessObservation(
        returncode=1,
        stdout="false when calling intentionally_wrong_abs(value = -1)\n",
        stderr="",
        duration_ms=12,
        timed_out=False,
        output_truncated=False,
    )
    report = CrossHairCheckAdapter(StubProcessRunner(observation)).check(
        assignment("crosshair_check", ("worker.verification.pilot_targets.intentionally_wrong_abs",)),
        repository=tmp_path,
    )
    assert report.status.value == "counterexample_found"
    assert report.counterexamples[0]["concrete_arguments"] == {"value": -1}
    assert report.counterexamples[0]["reproduction_command"][0] == "pytest"


def test_timeout_and_bounded_no_difference_never_become_passed(tmp_path: Path) -> None:
    timed_out = ProcessObservation(None, "", "", 30_000, True, False)
    report = CrossHairCheckAdapter(StubProcessRunner(timed_out)).check(
        assignment("crosshair_check", ("worker.verification.pilot_targets.clamp",)), repository=tmp_path
    )
    assert report.status.value == "timed_out"

    no_difference = ProcessObservation(0, "No differences found. (attempted 5 iterations)\n", "", 10, False, False)
    targets = (
        "worker.verification.pilot_targets.clamp",
        "worker.verification.pilot_targets.equivalent_clamp",
    )
    report = CrossHairDiffBehaviorAdapter(StubProcessRunner(no_difference)).compare(
        assignment("crosshair_diff", targets),
        repository=tmp_path,
        left_symbol=targets[0],
        right_symbol=targets[1],
    )
    assert report.status.value == "inconclusive"


def test_materializer_invalidates_changed_test_candidate() -> None:
    materializer = JsonCounterexampleMaterializer()
    raw = {
        "schema": "ananta.counterexample.v1",
        "run_ref": "RUN_test",
        "property_ref": "property",
        "target_symbol": "module.fn",
        "concrete_arguments": {"value": -1},
        "observed_result": {"returned": -1},
        "expected_invariant": "result >= 0",
    }
    result = materializer.materialize(raw, reproduction_command=["pytest", "-q", "test_regression.py"])
    assert materializer.promotion_is_current(result, result)
    changed = dict(result)
    changed["concrete_arguments"] = {"value": -2}
    assert not materializer.promotion_is_current(result, changed)


def test_process_runner_rejects_shell_escape_plugins_and_secret_injection(tmp_path: Path) -> None:
    runner = VerificationProcessRunner()
    with pytest.raises(ValueError, match="verification_executable_denied"):
        runner.run(["sh", "-c", "true"], repository=tmp_path, timeout_seconds=1, max_output_bytes=1000, memory_mb=128)
    with pytest.raises(ValueError, match="verification_crosshair_option_denied"):
        runner.run(
            ["crosshair", "check", "--unblock", "EVERYTHING"],
            repository=tmp_path,
            timeout_seconds=1,
            max_output_bytes=1000,
            memory_mb=128,
        )
    with pytest.raises(ValueError, match="verification_environment_key_denied"):
        runner.run(
            ["python", "-c", "pass"],
            repository=tmp_path,
            timeout_seconds=1,
            max_output_bytes=1000,
            memory_mb=128,
            extra_env={"API_TOKEN": "secret"},
        )


def test_fake_adapter_refuses_production_scope(tmp_path: Path) -> None:
    local = assignment()
    adapter = FakeVerificationRunnerAdapter(lambda _: None)
    synthetic_production = build_hub_evidence_assignment(
        run_id="RUN_synthetic_production_test",
        task_id="task-test",
        assignment_id="assignment-test",
        dispatch_lease_id="lease-test",
        source_ids=["SRC_synthetic_test"],
        evidence_scope="production",
        binding_digest="9" * 64,
    )
    production = type(local)(**{**local.to_dict(), "evidence_assignment": synthetic_production})
    with pytest.raises(ValueError, match="verification_fake_production_evidence_denied"):
        adapter.run(production, repository=tmp_path)


def test_fresh_process_reproduction_and_headless_promotion_are_bounded(tmp_path: Path) -> None:
    raw = {
        "schema": "ananta.counterexample.v1",
        "run_ref": "RUN_verification_test",
        "property_ref": "wrong-abs",
        "target_symbol": "worker.verification.pilot_targets.intentionally_wrong_abs",
        "concrete_arguments": {"value": -1},
        "observed_result": {"returned": -1},
        "expected_invariant": "result >= 0",
    }
    materializer = JsonCounterexampleMaterializer()
    command = (
        "python",
        "-c",
        "from worker.verification.pilot_targets import intentionally_wrong_abs; "
        "assert intentionally_wrong_abs(-1) >= 0",
    )
    counterexample = materializer.materialize(raw, reproduction_command=command)
    status, reason = CounterexampleReproducer().reproduce(
        assignment(), repository=Path(__file__).parents[2], command=command
    )
    assert status.value == "counterexample_found"
    assert reason == "counterexample_reproduced"
    blocked = VerificationPromotionService(auto_approval_enabled=False).decide(
        counterexample=counterexample,
        test_candidate=counterexample,
        reproduction_status=status.value,
        evidence_scope="test",
    )
    assert (blocked.status, blocked.reason_code) == ("blocked", "auto_approval_policy_disabled")
    approved = VerificationPromotionService(auto_approval_enabled=True).decide(
        counterexample=counterexample,
        test_candidate=counterexample,
        reproduction_status=status.value,
        evidence_scope="test",
    )
    assert approved.allowed is True


def test_revision_bound_diff_rejects_stale_worktrees_without_cleanup(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    patch = tmp_path / "patch"
    baseline.mkdir()
    patch.mkdir()
    calls = []

    class DiffAdapter:
        def compare(self, assignment, *, repository, left_symbol, right_symbol):
            calls.append((repository, left_symbol, right_symbol))
            return "report"

    revisions = {baseline.resolve(): "b" * 64, patch.resolve(): "a" * 64}
    runner = RevisionBoundDiffRunner(DiffAdapter(), revision_resolver=lambda root: revisions[root])
    pair = RevisionPair(baseline, patch, "b" * 64, "a" * 64)
    result = runner.compare(
        assignment("crosshair_diff", ("left.fn", "right.fn")),
        revisions=pair,
        left_symbol="left.fn",
        right_symbol="right.fn",
    )
    assert result == "report"
    assert baseline.exists() and patch.exists()
    stale = RevisionPair(baseline, patch, "c" * 64, "a" * 64)
    with pytest.raises(ValueError, match="verification_diff_baseline_revision_mismatch"):
        runner.compare(
            assignment("crosshair_diff", ("left.fn", "right.fn")),
            revisions=stale,
            left_symbol="left.fn",
            right_symbol="right.fn",
        )


def test_metrics_keep_timeout_unsupported_and_counterexamples_separate() -> None:
    summary = VerificationMetricsService.summarize(
        [
            {"status": "timed_out", "duration_ms": 10, "counterexamples": []},
            {"status": "unsupported", "duration_ms": 2, "counterexamples": []},
            {"status": "counterexample_found", "duration_ms": 3, "counterexamples": [{}]},
        ]
    )
    assert summary["by_status"] == {"counterexample_found": 1, "timed_out": 1, "unsupported": 1}
    assert summary["counterexamples"] == 1
