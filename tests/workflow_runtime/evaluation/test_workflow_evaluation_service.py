from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from agent.services.workflow_evaluation_service import (
    WorkflowEvaluationService,
    WorkflowJudgeAssessment,
    WorkflowJudgeRequest,
    load_workflow_evaluation_suite,
)
from agent.services.workflow_runtime.conformance import (
    RuntimeDifferentialEvaluator,
    RuntimeObservation,
)
from agent.services.workflow_runtime.reference_workflows import load_reference_workflows


def _observation(
    runtime_id: str,
    scenario_index: int = 0,
    *,
    terminal_status: str | None = None,
    capabilities: frozenset[str] | None = None,
) -> RuntimeObservation:
    scenario = load_reference_workflows()[scenario_index]
    return RuntimeObservation(
        runtime_id=runtime_id,
        terminal_status=terminal_status or scenario.invariants.terminal_statuses[0],
        capabilities=capabilities or frozenset(scenario.plan.capabilities),
        event_types=scenario.invariants.required_event_types,
        artifact_ids=scenario.invariants.required_artifacts,
        gate_ids=scenario.invariants.required_gates,
        side_effect_operations=scenario.invariants.side_effect_operations,
        policy_decisions=("policy.allowed",),
        budget_usage={"attempts": 1, "tokens": 0, "cost_micros": 0},
    )


@dataclass
class FakeJudge:
    assessment: WorkflowJudgeAssessment
    requests: list[WorkflowJudgeRequest] = field(default_factory=list)

    def evaluate(self, request: WorkflowJudgeRequest) -> WorkflowJudgeAssessment:
        self.requests.append(request)
        return self.assessment


def _judge(status: str = "passed") -> FakeJudge:
    return FakeJudge(
        WorkflowJudgeAssessment(
            status=status,
            model_ref="model://local/workflow-judge-fixture-v1",
            model_version="1.0.0",
            score=1.0 if status == "passed" else 0.5,
            reason_codes=(f"judge_{status}",),
        )
    )


def test_versioned_suite_binds_dataset_rubric_model_and_artifact_hashes() -> None:
    suite = load_workflow_evaluation_suite()

    assert suite.suite_version == "1.0.0"
    assert suite.dataset_version == "1.0.0"
    assert suite.rubric_version == "1.0.0"
    assert suite.artifact_version == "1.0.0"
    assert suite.catalog_sha256
    assert len(suite.suite_hash) == 64
    assert suite.models[0].model_version == "1.0.0"
    assert suite.models[0].descriptor_sha256
    assert suite.models[0].network_required is False


def test_deterministic_evaluation_is_network_free_and_byte_stable() -> None:
    scenario = load_reference_workflows()[0]
    service = WorkflowEvaluationService()

    first = service.evaluate(
        scenario,
        (_observation("native"), _observation("langgraph")),
    )
    second = service.evaluate(
        scenario,
        (_observation("langgraph"), _observation("native")),
    )

    assert first.status == "passed"
    assert first.release_eligible is True
    assert first.judge_requested is False
    assert first.to_dict() == second.to_dict()
    assert json.dumps(first.to_dict(), sort_keys=True, separators=(",", ":")) == json.dumps(
        second.to_dict(), sort_keys=True, separators=(",", ":")
    )


def test_differential_comparison_stops_at_capability_intersection() -> None:
    scenario = load_reference_workflows()[0]
    left = _observation("native")
    right = RuntimeObservation(
        runtime_id="langgraph",
        terminal_status="failed",
        capabilities=frozenset({"retrieval"}),
        event_types=("different",),
        artifact_ids=("different",),
    )

    issues = RuntimeDifferentialEvaluator().compare(
        left,
        right,
        required_capabilities=frozenset(scenario.plan.capabilities),
    )

    assert [issue.code for issue in issues] == ["runtime_pair_incompatible"]
    assert "terminal_status_drift" not in {issue.code for issue in issues}


@pytest.mark.parametrize("judge_status", ["unavailable", "disagreed", "failed"])
def test_requested_nonpassing_judge_degrades_without_runtime_release(judge_status: str) -> None:
    scenario = load_reference_workflows()[0]

    report = WorkflowEvaluationService().evaluate(
        scenario,
        (_observation("native"),),
        request_judge=True,
        judge=None if judge_status == "unavailable" else _judge(judge_status),
    )

    assert report.deterministic_status == "passed"
    assert report.status == "degraded"
    assert report.release_eligible is False
    assert report.judge_assessment.status == judge_status


def test_positive_judge_cannot_override_security_or_contract_failure() -> None:
    scenario = load_reference_workflows()[0]
    positive_judge = _judge("passed")

    report = WorkflowEvaluationService().evaluate(
        scenario,
        (_observation("native", terminal_status="failed"),),
        request_judge=True,
        judge=positive_judge,
    )

    assert positive_judge.requests[0].deterministic_status == "failed"
    assert report.judge_assessment.status == "passed"
    assert report.deterministic_status == "failed"
    assert report.status == "failed"
    assert report.release_eligible is False


def test_unknown_or_wrong_version_judge_model_is_unavailable_and_cannot_release() -> None:
    scenario = load_reference_workflows()[0]
    unknown = FakeJudge(
        WorkflowJudgeAssessment(
            status="passed",
            model_ref="model://unregistered/judge",
            model_version="99",
            score=1.0,
        )
    )

    report = WorkflowEvaluationService().evaluate(
        scenario,
        (_observation("native"),),
        request_judge=True,
        judge=unknown,
    )

    assert report.status == "degraded"
    assert report.release_eligible is False
    assert report.judge_assessment.status == "unavailable"
    assert report.judge_assessment.reason_codes == ("judge_model_ref_not_allowed",)


def test_passing_requested_judge_can_only_confirm_a_green_deterministic_gate() -> None:
    scenario = load_reference_workflows()[0]
    judge = _judge("passed")

    report = WorkflowEvaluationService().evaluate(
        scenario,
        (_observation("native"),),
        request_judge=True,
        judge=judge,
    )

    assert report.status == "passed"
    assert report.release_eligible is True
    request = judge.requests[0]
    assert request.scenario_id == "research"
    assert request.deterministic_report_hash
    assert not hasattr(request, "prompt")
    assert not hasattr(request, "credentials")
