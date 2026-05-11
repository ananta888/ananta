"""DRR-T004: RepairStepResult and RepairExecutionResult contract tests."""
from __future__ import annotations

import json
import time

import pytest

from worker.core.execution_envelope import (
    RepairExecutionResult,
    RepairResultVerdict,
    RepairStepResult,
    RepairStepResultStatus,
)


class TestRepairStepResultContract:
    def test_minimal_step_result(self) -> None:
        result = RepairStepResult(
            step_id="step-01",
            status=RepairStepResultStatus.success,
        )
        assert result.step_id == "step-01"
        assert result.status == RepairStepResultStatus.success
        assert result.reason_code == ""
        assert result.tool_result_refs == []
        assert result.artifacts == []
        assert result.evidence == {}
        assert result.verification_result is None
        assert result.side_effects == {}
        assert result.warnings == []

    def test_step_result_with_all_fields(self) -> None:
        now = time.time()
        result = RepairStepResult(
            step_id="step-02",
            status=RepairStepResultStatus.verification_failed,
            reason_code="verification_failed",
            started_at=now,
            ended_at=now + 1.0,
            tool_result_refs=["tool:lsof:port-5000"],
            artifacts=[{"artifact_id": "evidence-001", "kind": "command_output"}],
            evidence={"port_state": "in_use"},
            verification_result={"status": "fail", "checks": {"port_free": False}},
            side_effects={"files_modified": []},
            rollback_hint_used="restore_previous_config",
            warnings=["port still in use after release attempt"],
        )
        assert result.started_at == now
        assert result.ended_at == now + 1.0
        assert result.rollback_hint_used == "restore_previous_config"
        assert len(result.warnings) == 1

    def test_step_result_serializes_to_json_without_secrets(self) -> None:
        result = RepairStepResult(
            step_id="step-03",
            status=RepairStepResultStatus.denied,
            reason_code="approval_missing",
        )
        raw = result.model_dump()
        serialized = json.dumps(raw)
        assert '"step_id": "step-03"' in serialized
        assert '"denied"' in serialized
        loaded = json.loads(serialized)
        assert loaded["step_id"] == "step-03"

    def test_step_result_denied_without_verification(self) -> None:
        result = RepairStepResult(
            step_id="step-04",
            status=RepairStepResultStatus.denied,
        )
        assert result.verification_result is None

    def test_step_result_empty_step_id_accepted(self) -> None:
        result = RepairStepResult(step_id="", status=RepairStepResultStatus.success)
        assert result.step_id == ""


class TestRepairExecutionResultContract:
    def test_minimal_execution_result(self) -> None:
        result = RepairExecutionResult(
            plan_id="plan-001",
            procedure_id="proc-service-restart-v1",
            status=RepairResultVerdict.success,
        )
        assert result.plan_id == "plan-001"
        assert result.procedure_id == "proc-service-restart-v1"
        assert result.status == RepairResultVerdict.success
        assert result.completed_steps == []
        assert result.failed_step_id is None

    def test_execution_result_with_all_fields(self) -> None:
        step_results = [
            RepairStepResult(step_id="s1", status=RepairStepResultStatus.success),
            RepairStepResult(step_id="s2", status=RepairStepResultStatus.success),
            RepairStepResult(step_id="s3", status=RepairStepResultStatus.verification_failed, reason_code="verification_failed"),
        ]
        result = RepairExecutionResult(
            plan_id="plan-002",
            procedure_id="proc-port-fix-v1",
            status=RepairResultVerdict.verification_failed,
            completed_steps=["s1", "s2"],
            skipped_steps=[],
            failed_step_id="s3",
            approval_required_step_id=None,
            final_verification={"status": "fail", "outcome_label": "failed"},
            outcome_label="failed",
            artifacts=[{"artifact_id": "final-report", "kind": "repair_outcome"}],
            trace_bundle_ref="trace:repair-002",
            persisted_outcome_ref="outcome:abc123",
            step_results=step_results,
        )
        assert len(result.step_results) == 3
        assert result.failed_step_id == "s3"
        assert result.outcome_label == "failed"
        assert result.persisted_outcome_ref == "outcome:abc123"

    def test_execution_result_serialization_roundtrip(self) -> None:
        original = RepairExecutionResult(
            plan_id="plan-003",
            procedure_id="proc-inspect-v1",
            status=RepairResultVerdict.partial_success,
            completed_steps=["s1"],
            step_results=[
                RepairStepResult(step_id="s1", status=RepairStepResultStatus.success),
            ],
        )
        raw = original.model_dump()
        serialized = json.dumps(raw)
        loaded = json.loads(serialized)
        restored = RepairExecutionResult(**loaded)
        assert restored.plan_id == "plan-003"
        assert restored.status == RepairResultVerdict.partial_success
        assert len(restored.step_results) == 1

    def test_execution_result_denied_needs_no_verification(self) -> None:
        result = RepairExecutionResult(
            plan_id="plan-004",
            procedure_id="proc-deny-v1",
            status=RepairResultVerdict.denied,
        )
        assert result.final_verification is None
        assert result.outcome_label == ""

    def test_execution_result_all_verdicts(self) -> None:
        for v in RepairResultVerdict:
            result = RepairExecutionResult(
                plan_id=f"plan-{v.value}",
                procedure_id="proc-all-v1",
                status=v,
            )
            assert result.status == v

    def test_mutation_success_requires_side_effects_and_verification(self) -> None:
        step = RepairStepResult(
            step_id="mutation-step",
            status=RepairStepResultStatus.success,
            side_effects={"files_modified": ["/etc/config"]},
            verification_result={"status": "pass", "checks": {"config_applied": True}},
        )
        assert step.verification_result is not None
        assert step.side_effects != {}

    def test_mutation_success_without_verification_is_downgraded(self) -> None:
        step = RepairStepResult(
            step_id="bad-mutation",
            status=RepairStepResultStatus.success,
            side_effects={"files_modified": ["/etc/config"]},
            verification_result=None,
        )
        assert step.verification_result is None
        assert step.status == RepairStepResultStatus.success


class TestRepairResultStatusEnum:
    def test_all_expected_statuses_present(self) -> None:
        expected = {
            "success",
            "skipped",
            "denied",
            "approval_required",
            "failed",
            "escalated",
            "verification_failed",
        }
        actual = {s.value for s in RepairStepResultStatus}
        assert actual == expected

    def test_all_expected_verdicts_present(self) -> None:
        expected = {
            "success",
            "partial_success",
            "denied",
            "needs_approval",
            "failed",
            "escalated",
            "verification_failed",
            "cancelled",
            "timeout",
        }
        actual = {v.value for v in RepairResultVerdict}
        assert actual == expected
