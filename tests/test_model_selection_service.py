from __future__ import annotations

import pytest

from agent.services.model_selection_service import (
    CognitiveStyleFitPolicy,
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
    ModelRoutingConflict,
)
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    CognitiveStyleVector,
    ModelAssignment,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingMutationCommand,
    RoleStyleTarget,
    StyleRange,
)


def _service():
    repository = InMemoryModelRoutingConfigurationRepository()
    return ModelRoutingAssignmentService(
        repository=repository,
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local-fast", "local-heavy"),
    )


def test_assignment_mutation_is_atomic_and_revision_bound():
    service = _service()
    updated = service.apply(ModelRoutingMutationCommand(
        schema="ananta.model-routing-mutation-command.v1",
        expected_revision=0,
        assignments=(ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-heavy", fallback_group_id="local",
        ),),
        fallback_groups=(ModelFallbackGroup(
            group_id="local",
            candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
        ),),
    ))
    assert updated.revision == 1
    assert updated.assignments[0].profile_id == "local-heavy"

    with pytest.raises(ModelRoutingConflict) as error:
        service.apply(ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=0,
        ))
    assert error.value.current_revision == 1


def test_assignment_rejects_unknown_consumer_and_profile():
    service = _service()
    for assignment, reason in (
        (ModelAssignment(consumer_id="unknown", scope="global", mode="inherit"), "model_consumer_unknown"),
        (ModelAssignment(consumer_id="task.coding", scope="global", mode="profile", profile_id="cloud-unknown"), "model_assignment_profile_unknown"),
    ):
        with pytest.raises(ValueError, match=reason):
            service.apply(ModelRoutingMutationCommand(
                schema="ananta.model-routing-mutation-command.v1",
                expected_revision=0,
                assignments=(assignment,),
            ))


def test_fallback_cannot_continue_after_policy_block():
    with pytest.raises(ValueError, match="model_fallback_policy_block_must_be_terminal"):
        ModelFallbackGroup(
            group_id="unsafe",
            candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
            stop_on_policy_block=False,
        )


def test_style_fit_is_soft_and_never_grants_authority():
    profile = AgentStyleProfile(
        profile_id="measured-local-heavy",
        model_profile_id="local-heavy",
        scores=CognitiveStyleVector(
            rule_correctness=.9, truth_exploration=.75, initiative_assertiveness=.5,
        ),
        confidence=.8, sample_count=20, benchmark_revision="style-v1",
        measured_at="2026-08-25T00:00:00Z", source="measured",
        model_revision="sha256:base", quantization="q8", runtime="llamacpp",
        prompt_digest="sha256:prompt", tool_mode="prompt_json",
        sampling_digest="sha256:sampling",
    )
    target = RoleStyleTarget(
        target_id="reviewer-v1", role_id="reviewer",
        rule_correctness=StyleRange(minimum=.7, maximum=1, weight=1),
        truth_exploration=StyleRange(minimum=.7, maximum=1, weight=2),
        initiative_assertiveness=StyleRange(minimum=.3, maximum=.7, weight=1),
    )
    decision = CognitiveStyleFitPolicy().evaluate(profile, target)
    assert decision.score == .8
    assert decision.eligible is True
    assert decision.grants_authority is False
