from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.services.cognitive_style_service import (
    CognitiveStyleConflict,
    CognitiveStyleRankingPolicy,
    CognitiveStyleService,
    InMemoryCognitiveStyleStateRepository,
)
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext
from ananta_contracts.cognitive_style import (
    CognitiveStyleMutationCommand,
    StyleBenchmarkResult,
    StyleEvolutionProposal,
    StyleEvolutionTransitionCommand,
    StyleMismatchEvidence,
    TeamStyleMember,
)
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    CognitiveStyleVector,
)


def _measured(profile_id: str, model_profile_id: str, scores: tuple[float, float, float]):
    return AgentStyleProfile(
        profile_id=profile_id,
        model_profile_id=model_profile_id,
        scores=CognitiveStyleVector(
            rule_correctness=scores[0],
            truth_exploration=scores[1],
            initiative_assertiveness=scores[2],
        ),
        confidence=.9,
        sample_count=24,
        benchmark_revision="behavior-style-v1",
        measured_at=datetime.now(timezone.utc).isoformat(),
        source="measured",
        model_revision="model-r1",
        quantization="q8",
        runtime="llamacpp",
        backend_id="lmstudio",
        prompt_digest="sha256:system",
        role_prompt_digest="sha256:role",
        tool_mode="prompt_json",
        sampling_digest="sha256:sampling",
        evidence_refs=(f"style-observation://{profile_id}",),
    )


def test_defaults_expose_mixed_role_targets_and_permission_neutral_overlays():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    read = service.read()

    roles = {item.role_id for item in read.configuration.role_targets}
    assert {"implementer", "qa", "reviewer", "challenger", "planner", "scrum_master"} <= roles
    assert all(item.permission_delta == "none" for item in read.configuration.overlays)
    assert "keine psychologische Diagnose" in read.heuristic_notice


def test_mutation_is_atomic_revision_bound_and_project_target_does_not_mutate_default():
    repo = InMemoryCognitiveStyleStateRepository()
    service = CognitiveStyleService(repo)
    current = service.read().configuration
    default = next(item for item in current.role_targets if item.role_id == "reviewer")
    project = default.model_copy(update={
        "target_id": "project.review.v1", "project_id": "ananta"
    })
    updated = service.mutate(CognitiveStyleMutationCommand(
        expected_revision=0,
        profiles=(),
        role_targets=(*current.role_targets, project),
        overlays=current.overlays,
    ))

    assert updated.revision == 1
    assert service.resolve_target("reviewer", project_id="ananta") == project
    assert service.resolve_target("reviewer") == default
    with pytest.raises(CognitiveStyleConflict):
        service.mutate(CognitiveStyleMutationCommand(expected_revision=0))


def test_active_profiles_are_unique_per_model_and_rebenchmark_moves_old_context_to_history():
    repo = InMemoryCognitiveStyleStateRepository()
    service = CognitiveStyleService(repo)
    old = _measured("old", "model-a", (.8, .4, .3))
    current = service.read().configuration
    service.mutate(CognitiveStyleMutationCommand(
        expected_revision=0,
        profiles=(old,),
        role_targets=current.role_targets,
        overlays=current.overlays,
    ))
    replacement = old.model_copy(update={
        "profile_id": "new",
        "model_revision": "model-r2",
        "sampling_digest": "sha256:sampling-v3",
    })

    updated = service.record_benchmark_result(
        StyleBenchmarkResult(
            profile=replacement,
            observations=(),
            prompt_sensitivity={
                "rule_correctness": 0,
                "truth_exploration": 0,
                "initiative_assertiveness": 0,
            },
        ),
        expected_revision=1,
    )
    read = service.read()

    assert updated.profiles == (replacement,)
    assert read.profile_history == (old,)


def test_manual_mutation_rejects_two_active_profiles_for_the_same_model():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    old = _measured("old", "model-a", (.8, .4, .3))
    duplicate = old.model_copy(update={"profile_id": "duplicate"})

    with pytest.raises(ValueError, match="active_style_model_profile_duplicate"):
        service.mutate(CognitiveStyleMutationCommand(
            expected_revision=0,
            profiles=(old, duplicate),
        ))


def test_diversity_reports_homogeneity_without_overriding_hard_policy():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    current = service.read().configuration
    service.mutate(CognitiveStyleMutationCommand(
        expected_revision=0,
        profiles=(
            _measured("a", "model-a", (.8, .4, .4)),
            _measured("b", "model-b", (.81, .41, .39)),
        ),
        role_targets=current.role_targets,
        overlays=current.overlays,
    ))

    report = service.diversity((
        TeamStyleMember(agent_id="one", role_id="implementer", model_profile_id="model-a"),
        TeamStyleMember(agent_id="two", role_id="reviewer", model_profile_id="model-b"),
    ))

    assert report.classification == "homogeneous"
    assert report.capability_or_security_overridden is False


def test_style_ranking_is_soft_and_missing_profile_remains_a_candidate():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    target = service.resolve_target("reviewer")
    models = (
        ModelProfile(profile_id="rule", provider_id="lmstudio", model="rule"),
        ModelProfile(profile_id="explore", provider_id="lmstudio", model="explore"),
        ModelProfile(profile_id="unknown", provider_id="lmstudio", model="unknown"),
    )
    policy = CognitiveStyleRankingPolicy(
        profiles=(
            _measured("rule-style", "rule", (.95, .3, .4)),
            _measured("explore-style", "explore", (.8, .9, .55)),
        ),
        targets=(target,),
        weight=.5,
    )

    ranked = policy.rank_profiles(models, role_id="reviewer")

    assert ranked[0].profile.profile_id == "explore"
    assert {item.profile.profile_id for item in ranked} == {"rule", "explore", "unknown"}
    assert next(item for item in ranked if item.profile.profile_id == "unknown").reason == "style_profile_unavailable"


def test_style_ranking_emits_one_bounded_outcome_per_decision():
    class _Observer:
        def __init__(self):
            self.outcomes = []

        def record(self, outcome):
            self.outcomes.append(outcome)

    observer = _Observer()
    target = CognitiveStyleService(
        InMemoryCognitiveStyleStateRepository()
    ).resolve_target("reviewer")
    policy = CognitiveStyleRankingPolicy(
        profiles=(_measured("review-style", "review", (.8, .9, .5)),),
        targets=(target,), observer=observer,
    )

    policy.rank_profiles((ModelProfile(
        profile_id="review", provider_id="lmstudio", model="review",
    ),), role_id="reviewer")

    assert observer.outcomes == ["applied"]


def test_style_fit_runs_after_hard_capability_gates_and_cannot_restore_rejected_model():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    target = service.resolve_target("reviewer")
    incompatible = ModelProfile(
        profile_id="stylish", provider_id="lmstudio", model="stylish",
        model_role="reviewer", supports_tools=False,
    )
    compatible = ModelProfile(
        profile_id="tool-safe", provider_id="lmstudio", model="tool-safe",
        model_role="reviewer", supports_tools=True, tool_calling_mode="native_tools",
    )
    policy = CognitiveStyleRankingPolicy(
        profiles=(
            _measured("stylish-score", "stylish", (.9, .95, .6)),
            _measured("safe-score", "tool-safe", (.7, .7, .4)),
        ),
        targets=(target,),
        weight=1,
    )
    result = ModelProfileResolver(
        [incompatible, compatible], style_ranking=policy,
    ).resolve(RoutingContext(
        model_role="reviewer", requires_tools=True,
        metadata={"style_role_id": "reviewer"},
    ))

    assert result.profile == compatible
    assert any(
        item.profile_id == "stylish" and "tools_required" in item.reason
        for item in result.decisions
    )
    assert any(item.source == "cognitive_style_soft_ranking" for item in result.decisions)


def test_mismatch_is_hypothesis_only_and_evolver_requires_reviewed_lifecycle():
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    service.record_mismatch(StyleMismatchEvidence(
        evidence_id="evidence-1", agent_id="agent-1", role_id="reviewer",
        model_profile_id="model-a", signal="overthinking",
        observed_at="2026-08-25T00:00:00Z", correlation_score=.4,
        hypothesis="Exploration könnte zu breit sein.",
        alternative_causes=("Aufgabe war unklar", "Kontext war unvollständig"),
    ), expected_revision=0)
    proposal = StyleEvolutionProposal(
        proposal_id="proposal-1", proposal_type="style_target",
        hypothesis="Engerer Zielbereich reduziert Overthinking.",
        expected_effect="Weniger Rework bei gleicher Gate-Qualität.",
        experiment_id="experiment-1", payload={"role_id": "reviewer"},
        rollback_payload={"restore_target": "standard.reviewer.v1"},
    )
    service.add_proposal(proposal, expected_revision=1)
    service.transition_proposal(
        "proposal-1",
        StyleEvolutionTransitionCommand(expected_status="proposed", target_status="validated"),
        expected_revision=2,
    )
    with pytest.raises(ValueError, match="review_required"):
        service.transition_proposal(
            "proposal-1",
            StyleEvolutionTransitionCommand(expected_status="validated", target_status="approved"),
            expected_revision=3,
        )
    read = service.read()
    assert read.mismatch_evidence[0].causes_reclassification is False
    assert read.evolution_proposals[0].status == "validated"
