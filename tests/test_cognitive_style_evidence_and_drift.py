from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.services.cognitive_style_drift_service import CognitiveStyleDriftService
from agent.services.cognitive_style_evidence_service import (
    CognitiveStyleRetrospectiveService,
    ComplementaryStyleExperimentService,
)
from agent.services.cognitive_style_overlay_comparison_service import (
    CognitiveStyleOverlayComparisonService,
)
from agent.services.cognitive_style_rebenchmark_service import (
    CognitiveStyleRebenchmarkPlanner,
)
from agent.services.cognitive_style_service import standard_role_style_overlays
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import CognitiveStyleFitPolicy
from ananta_contracts.cognitive_style import (
    ComplementaryStyleExperimentCommand,
    StyleExperimentMetrics,
    StyleMeasurementContext,
    StyleOverlayComparisonCommand,
    StyleRebenchmarkDueCommand,
    StyleRetrospectiveSignal,
)
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    CognitiveStyleVector,
    RoleStyleTarget,
    StyleRange,
)


def _profile(
    profile_id: str,
    scores: tuple[float, float, float],
    *,
    revision: str = "r1",
    measured_at: str | None = None,
    role_digest: str = "sha256:role",
) -> AgentStyleProfile:
    return AgentStyleProfile(
        profile_id=profile_id,
        model_profile_id="model-a",
        scores=CognitiveStyleVector(
            rule_correctness=scores[0],
            truth_exploration=scores[1],
            initiative_assertiveness=scores[2],
        ),
        confidence=.9,
        sample_count=48,
        benchmark_revision="behavior-style-v1",
        measured_at=measured_at or datetime.now(timezone.utc).isoformat(),
        source="measured",
        model_revision=revision,
        quantization="q8",
        runtime="llamacpp",
        backend_id="lmstudio",
        prompt_digest="sha256:system",
        role_prompt_digest=role_digest,
        tool_mode="prompt_json",
        sampling_digest="sha256:sampling",
        evidence_refs=(f"style-observation://{profile_id}",),
    )


def _context(revision: str = "r1") -> StyleMeasurementContext:
    return StyleMeasurementContext(
        model_profile_id="model-a",
        model_revision=revision,
        quantization="q8",
        runtime="llamacpp",
        backend_id="lmstudio",
        system_prompt_digest="sha256:system",
        role_prompt_digest="sha256:role",
        tool_mode="prompt_json",
        sampling_digest="sha256:sampling",
    )


def test_drift_never_overwrites_history_and_marks_revision_or_age_for_rebenchmark():
    current = _profile("current", (.8, .5, .4))
    stale = _profile(
        "stale", (.8, .5, .4),
        measured_at=(datetime.now(timezone.utc) - timedelta(days=120)).isoformat(),
    )
    service = CognitiveStyleDriftService()

    revision_report = service.evaluate(profiles=(current,), contexts=(_context("r2"),))
    stale_report = service.evaluate(profiles=(stale,), contexts=(_context(),))

    assert revision_report.entries[0].status == "model_revision_drift"
    assert stale_report.entries[0].status == "stale"
    assert revision_report.rebenchmark_due_count == stale_report.rebenchmark_due_count == 1


def test_rebenchmark_planner_schedules_only_due_registered_profiles():
    current = _profile("current", (.8, .5, .4))
    command = StyleRebenchmarkDueCommand(
        expected_revision=4,
        contexts=(
            _context(),
            _context().model_copy(update={"model_profile_id": "model-missing"}),
        ),
    )
    schedule = CognitiveStyleRebenchmarkPlanner().plan(
        command=command,
        style_profiles=(current,),
        model_profiles=(ModelProfile(
            profile_id="model-a", provider_id="lmstudio", model="lfm",
        ),),
    )

    assert schedule.drift.rebenchmark_due_count == 1
    assert schedule.work_items == ()
    assert schedule.skipped_profile_ids == ("model-missing",)


def test_rebenchmark_planner_builds_fixed_suite_for_revision_drift():
    schedule = CognitiveStyleRebenchmarkPlanner().plan(
        command=StyleRebenchmarkDueCommand(
            expected_revision=0,
            contexts=(_context("r2"),),
        ),
        style_profiles=(_profile("old", (.8, .5, .4), revision="r1"),),
        model_profiles=(ModelProfile(
            profile_id="model-a", provider_id="lmstudio", model="lfm",
        ),),
    )

    assert len(schedule.work_items) == 1
    assert len(schedule.work_items[0].plan.variants) == 6
    assert schedule.work_items[0].plan.repeats == 2


def test_overlay_before_after_comparison_requires_same_base_context_and_never_changes_permissions():
    report = CognitiveStyleOverlayComparisonService().compare(
        command=StyleOverlayComparisonCommand(
            baseline_profile_id="before",
            overlay_profile_id="after",
            overlay_id="standard.evidence-review.v1",
        ),
        profiles=(
            _profile("before", (.8, .5, .4), role_digest="sha256:plain"),
            _profile("after", (.8, .8, .4), role_digest="sha256:overlay"),
        ),
        overlays=standard_role_style_overlays(),
    )

    assert report.comparable is True
    assert report.score_deltas["truth_exploration"] == .3
    assert report.reinforced_dimensions_improved == ("truth_exploration",)
    assert report.permission_delta == "none"


def test_retrospective_keeps_alternative_causes_and_only_creates_reviewed_proposal():
    service = CognitiveStyleRetrospectiveService()
    report = service.analyze((StyleRetrospectiveSignal(
        agent_id="agent-a",
        role_id="reviewer",
        model_profile_id="model-a",
        signal="overthinking",
        observed_at="2026-08-25T00:00:00Z",
        severity=.6,
        evidence_refs=("RUN_supplied_by_caller",),
    ),))
    evidence = report.hypotheses[0]
    proposal = service.proposal_from_evidence(
        evidence,
        proposal_id="proposal-a",
        experiment_id="experiment-a",
    )

    assert report.automatic_reclassification_performed is False
    assert report.causal_claim_made is False
    assert len(evidence.alternative_causes) >= 3
    assert evidence.evidence_refs == ("RUN_supplied_by_caller",)
    assert proposal.review_required is True
    assert proposal.status == "proposed"


def test_complementary_experiment_supports_and_can_falsify_hypothesis():
    service = ComplementaryStyleExperimentService()
    supported = service.evaluate(ComplementaryStyleExperimentCommand(
        experiment_id="experiment-good",
        complementary=StyleExperimentMetrics(
            quality_score=.9, rework_count=1, cost_units=12,
            duration_seconds=40, gates_passed=4, gates_total=4,
        ),
        homogeneous_control=StyleExperimentMetrics(
            quality_score=.7, rework_count=2, cost_units=10,
            duration_seconds=35, gates_passed=3, gates_total=4,
        ),
    ))
    falsified = service.evaluate(ComplementaryStyleExperimentCommand(
        experiment_id="experiment-bad",
        complementary=StyleExperimentMetrics(
            quality_score=.5, rework_count=3, cost_units=12,
            duration_seconds=50, gates_passed=2, gates_total=4,
        ),
        homogeneous_control=StyleExperimentMetrics(
            quality_score=.8, rework_count=1, cost_units=10,
            duration_seconds=35, gates_passed=4, gates_total=4,
        ),
    ))

    assert supported.outcome == "supported"
    assert falsified.outcome == "falsified"
    assert supported.security_or_capability_gate_bypassed is False


def test_must_have_and_avoid_ranges_are_explainable_soft_penalties_only():
    target = RoleStyleTarget(
        target_id="target-review",
        role_id="reviewer",
        rule_correctness=StyleRange(minimum=.5, maximum=1),
        truth_exploration=StyleRange(minimum=.6, maximum=1),
        initiative_assertiveness=StyleRange(minimum=.2, maximum=.8),
        must_have={"truth_exploration": StyleRange(minimum=.8, maximum=1, weight=3)},
        avoid_ranges={
            "initiative_assertiveness": (
                StyleRange(minimum=.8, maximum=1, weight=5),
            )
        },
    )
    decision = CognitiveStyleFitPolicy().evaluate(
        _profile("risky", (.8, .4, .9)), target
    )

    names = {name for name, _score in decision.contributions}
    assert "must_have:truth_exploration" in names
    assert "avoid:initiative_assertiveness" in names
    assert decision.eligible is True
    assert decision.grants_authority is False
