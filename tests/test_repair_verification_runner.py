"""DRR-T022: RepairVerificationRunner tests."""
from __future__ import annotations

import pytest

from worker.core.execution_envelope import RepairStep
from worker.repair.repair_verification_runner import (
    ProbeType,
    VerificationResult,
    run_verification_probe,
    verify_final,
    verify_step,
)


class TestProbeTypes:
    def test_health_check_probe(self) -> None:
        result = run_verification_probe(probe_type="health_check", target="api", expected="healthy")
        assert result.passed
        assert result.probe_type == ProbeType.health_check
        assert result.reason_code == "pass"

    def test_service_status_probe(self) -> None:
        result = run_verification_probe(probe_type="service_status", target="nginx")
        assert result.passed

    def test_command_result_probe(self) -> None:
        result = run_verification_probe(probe_type="command_result", target="ls")
        assert result.passed
        assert result.evidence.get("exit_code") == 0

    def test_functional_probe(self) -> None:
        result = run_verification_probe(probe_type="functional_probe", target="app")
        assert result.passed

    def test_unsupported_probe_denied(self) -> None:
        result = run_verification_probe(probe_type="unsupported_type", target="x")
        assert not result.passed
        assert result.reason_code == "unsupported_probe_type"


class TestVerifyStep:
    def test_verification_not_required(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Inspect",
            mutation_candidate=False,
            verification_required=False,
        )
        result = verify_step(step=step)
        assert result.status.value == "success"
        assert result.reason_code == "verification_not_required"

    def test_verification_required_passes(self) -> None:
        step = RepairStep(
            step_id="s2",
            title="Check service",
            action_class="service_status",
            mutation_candidate=False,
            verification_required=True,
            expected_verification="service_running",
        )
        result = verify_step(step=step)
        assert result.status.value == "success"
        assert result.verification_result is not None
        assert result.verification_result.get("passed") is True

    def test_verification_fails_for_mutation(self) -> None:
        step = RepairStep(
            step_id="s3",
            title="Mutate",
            action_class="port_conflict_resolution",
            mutation_candidate=True,
            verification_required=True,
        )
        result = verify_step(step=step, verification_plan=["s3"])
        assert result.status.value == "success"
        assert result.verification_result is not None


class TestVerifyFinal:
    def test_all_passed(self) -> None:
        from worker.core.execution_envelope import RepairStepResult, RepairStepResultStatus

        results = [
            RepairStepResult(step_id="s1", status=RepairStepResultStatus.success),
            RepairStepResult(step_id="s2", status=RepairStepResultStatus.success),
        ]
        summary = verify_final(step_results=results, procedure_id="proc-test")
        assert summary["all_passed"] is True
        assert summary["passed_count"] == 2

    def test_verification_failed(self) -> None:
        from worker.core.execution_envelope import RepairStepResult, RepairStepResultStatus

        results = [
            RepairStepResult(step_id="s1", status=RepairStepResultStatus.success),
            RepairStepResult(step_id="s2", status=RepairStepResultStatus.verification_failed),
        ]
        summary = verify_final(step_results=results, procedure_id="proc-test")
        assert summary["verification_failed"] is True
        assert summary["all_passed"] is False
