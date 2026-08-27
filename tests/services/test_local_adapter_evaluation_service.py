from __future__ import annotations

from agent.services.local_adapter_evaluation_service import (
    LocalAdapterEvaluationService,
    ToolEvaluationCase,
)


def _case(slice_id: str, *, confidence=0.95, candidate=None):
    expected = {"query": slice_id}
    decision = candidate if candidate is not None else {"tool": "lookup", "arguments": expected}
    return ToolEvaluationCase(
        case_id=f"case-{slice_id}",
        slice_id=slice_id,
        allowed_tools=frozenset({"lookup"}),
        expected_tool="lookup",
        required_arguments={"query": str},
        expected_arguments=expected,
        base_decision={"tool": "lookup", "arguments": expected},
        candidate_decisions=(decision, decision),
        candidate_confidence=confidence,
        latency_ms=10,
        memory_bytes=100,
    )


def test_independent_evaluator_requires_all_slices_and_perfect_structure() -> None:
    report = LocalAdapterEvaluationService().evaluate(
        [_case(slice_id) for slice_id in sorted(LocalAdapterEvaluationService.REQUIRED_SLICES)],
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256="c" * 64,
        policy_sha256="d" * 64,
        evaluation_seed=42,
    )

    assert report.json_validity == 1.0
    assert report.known_tool_rate == 1.0
    assert report.required_fields_rate == 1.0
    assert report.argument_type_rate == 1.0
    assert report.known_arguments_rate == 1.0
    assert report.deterministic is True
    assert report.confidence_calibrated is True
    assert report.confidence_brier_score < report.confidence_max_brier_score
    assert report.passed_required_slices is True


def test_null_confidence_is_never_treated_as_calibrated() -> None:
    report = LocalAdapterEvaluationService().evaluate(
        [_case("golden", confidence=None)],
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256="c" * 64,
        policy_sha256="d" * 64,
        evaluation_seed=42,
    )

    assert report.confidence_calibrated is False
    assert report.passed_required_slices is False


def test_confidence_range_alone_is_not_calibration() -> None:
    report = LocalAdapterEvaluationService().evaluate(
        [_case("golden", confidence=0.1)],
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256="c" * 64,
        policy_sha256="d" * 64,
        evaluation_seed=42,
    )

    assert report.confidence_brier_score == 0.81
    assert report.confidence_calibrated is False


def test_unknown_arguments_and_noncanonical_candidates_fail_closed() -> None:
    unknown = _case(
        "golden",
        confidence=0.1,
        candidate={"tool": "lookup", "arguments": {"query": "golden", "secret_extra": "x"}},
    )
    malformed = _case(
        "malformed_schema",
        confidence=0.1,
        candidate={"tool": "lookup", "arguments": {"query": float("nan")}},
    )
    report = LocalAdapterEvaluationService().evaluate(
        [unknown, malformed],
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256="c" * 64,
        policy_sha256="d" * 64,
        evaluation_seed=42,
    )

    assert report.known_arguments_rate == 0.0
    assert report.json_validity == 0.5
    assert report.argument_match == 0.0
