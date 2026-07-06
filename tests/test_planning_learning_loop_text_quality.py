from agent.services.planning_learning_loop_service import PlanningLearningLoopService


def _group(**overrides):
    value = {
        "run_count": 12,
        "parse_success_rate": 1,
        "validation_success_rate": 1,
        "materialization_success_rate": 1,
        "repair_rate": 0,
        "trend_direction": "stable",
        "quality_score": 0.9,
        "text_quality_comparable": True,
        "text_quality_completed_count": 12,
        "average_slop_score": 0.7,
        "average_depth_score": 0.4,
        "average_style_fit_score": 0.5,
    }
    value.update(overrides)
    return value


def test_comparable_text_quality_can_trigger_review_candidate():
    qualifies, reasons = PlanningLearningLoopService()._qualifies_for_learning(
        group=_group(),
        learning={"min_runs": 8, "min_failures": 3, "min_text_quality_runs": 10},
    )
    assert qualifies
    assert {"slop_score_high", "depth_score_low", "style_fit_low"} <= set(reasons)


def test_small_or_incomparable_samples_do_not_trigger():
    for group in (
        _group(text_quality_completed_count=2),
        _group(text_quality_comparable=False),
    ):
        qualifies, reasons = PlanningLearningLoopService()._qualifies_for_learning(
            group=group,
            learning={"min_runs": 8, "min_failures": 1, "min_text_quality_runs": 10},
        )
        assert not qualifies
        assert reasons == ["metrics_within_bounds"]


def test_missing_text_quality_never_becomes_zero_score():
    service = PlanningLearningLoopService()
    assert service._quality_score({"quality_score": 0.82}) == 0.82
