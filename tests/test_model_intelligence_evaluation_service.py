from __future__ import annotations

from agent.services.model_intelligence_evaluation_service import (
    EvaluationProfile,
    EvaluationRun,
    HardwareIdentity,
    MetricDefinition,
    ModelEvaluationComparisonService,
    RuntimeIdentity,
    latency_metrics,
)


def _profile(*, dimension: str = "quality") -> EvaluationProfile:
    return EvaluationProfile(
        profile_id="fixture-quality",
        version="1",
        seed=7,
        prompt_digest="a" * 64,
        repetitions=3,
        warmups=1,
        comparison_dimension=dimension,
        require_same_runtime=True,
        metrics=(
            MetricDefinition(
                name="accuracy",
                unit="ratio",
                higher_is_better=True,
                absolute_tolerance=0.001,
                regression_threshold=0.02,
            ),
        ),
    )


def _run(
    run_id: str,
    value: float,
    *,
    profile: EvaluationProfile | None = None,
    hardware_profile: str = "cpu-small-v1",
) -> EvaluationRun:
    return EvaluationRun(
        schema_version="evaluation_run.v1",
        run_id=run_id,
        model_id=f"model:{run_id}",
        artifact_digest="b" * 64,
        profile=profile or _profile(),
        runtime=RuntimeIdentity(
            provider="ananta",
            version="1",
            backend="cpu",
            configuration_digest="c" * 64,
        ),
        hardware=HardwareIdentity(
            profile_id=hardware_profile,
            cpu="fixture-cpu",
            accelerator=None,
            memory_bytes=1024,
        ),
        metrics={"accuracy": value},
    )


def test_compatible_comparison_reports_threshold_regression() -> None:
    result = ModelEvaluationComparisonService().compare(
        baseline=_run("baseline", 0.9),
        candidate=_run("candidate", 0.8),
    ).to_dict()

    assert result["status"] == "comparable"
    assert result["metrics"][0]["regression"] is True
    assert result["metrics"][0]["delta"] < 0


def test_incompatible_profiles_never_produce_a_ranking() -> None:
    changed = EvaluationProfile(
        profile_id="fixture-quality",
        version="2",
        seed=7,
        prompt_digest="a" * 64,
        repetitions=3,
        warmups=1,
        comparison_dimension="quality",
        require_same_runtime=True,
        metrics=_profile().metrics,
    )

    result = ModelEvaluationComparisonService().compare(
        baseline=_run("baseline", 0.9),
        candidate=_run("candidate", 0.95, profile=changed),
    ).to_dict()

    assert result["status"] == "incomparable"
    assert result["reason_code"] == "evaluation_profile_incompatible"
    assert result["metrics"] == []


def test_performance_profile_requires_identical_hardware() -> None:
    performance = _profile(dimension="performance")

    result = ModelEvaluationComparisonService().compare(
        baseline=_run("baseline", 0.9, profile=performance),
        candidate=_run(
            "candidate",
            0.9,
            profile=performance,
            hardware_profile="cpu-large-v1",
        ),
    ).to_dict()

    assert result["status"] == "incomparable"
    assert result["reason_code"] == "evaluation_hardware_incompatible"


def test_latency_metrics_use_documented_p50_and_nearest_rank_p95() -> None:
    metrics = latency_metrics([1, 2, 3, 4, 100])

    assert metrics == {
        "latency_p50_ms": 3.0,
        "latency_p95_ms": 100.0,
    }
