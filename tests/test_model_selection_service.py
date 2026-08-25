from __future__ import annotations

import pytest

from agent.services.model_selection_service import (
    CognitiveStyleFitPolicy,
    EffectiveModelRoutingService,
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
    ModelRoutingConflict,
)
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    CognitiveStyleVector,
    ModelAssignment,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingMutationCommand,
    ModelRoutingDryRunCommand,
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


def test_assignment_rejects_raw_model_without_registered_profile_mapping():
    with pytest.raises(ValueError, match="model_assignment_model_unknown"):
        _service().apply(ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=0,
            assignments=(ModelAssignment(
                consumer_id="task.coding",
                scope="global",
                mode="model",
                provider_id="lmstudio",
                model_id="unregistered",
            ),),
        ))


def test_fallback_cannot_continue_after_policy_block():
    with pytest.raises(ValueError, match="model_fallback_policy_block_must_be_terminal"):
        ModelFallbackGroup(
            group_id="unsafe",
            candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
            stop_on_policy_block=False,
        )


def test_fallback_escalation_cannot_cycle_or_reference_unknown_profile():
    with pytest.raises(ValueError, match="model_fallback_escalation_cycle"):
        ModelFallbackGroup(
            group_id="cycle",
            candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
            on_exhausted="escalate",
            escalation_profile_id="local-heavy",
        )
    with pytest.raises(ValueError, match="model_fallback_escalation_profile_unknown"):
        _service().apply(ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=0,
            fallback_groups=(ModelFallbackGroup(
                group_id="unknown-escalation",
                candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
                on_exhausted="escalate",
                escalation_profile_id="missing",
            ),),
        ))


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


def _effective_service(configuration):
    repository = InMemoryModelRoutingConfigurationRepository(configuration)
    resolver = ModelProfileResolver(profiles=[
        ModelProfile(
            profile_id="local-fast", provider_id="lmstudio", model="lfm",
            model_role="coder", local=True, supports_json=True,
            tool_calling_mode="prompt_json",
        ),
        ModelProfile(
            profile_id="local-heavy", provider_id="lmstudio", model="kat",
            model_role="coder", local=True, supports_json=True,
            tool_calling_mode="prompt_json",
        ),
        ModelProfile(
            profile_id="cloud-code", provider_id="openai", model="cloud",
            model_role="coder", cloud=True, cloud_allowed=True,
            block_secret_context=True,
        ),
    ])
    return EffectiveModelRoutingService(
        repository=repository,
        consumers=ModelConsumerRegistry.defaults(),
        resolver=resolver,
    )


def test_dry_run_uses_most_specific_assignment_and_real_resolver_trace():
    configuration = _service().read().model_copy(update={
        "revision": 4,
        "assignments": (
            ModelAssignment(
                consumer_id="task.coding", scope="global", mode="profile",
                profile_id="local-fast",
            ),
            ModelAssignment(
                consumer_id="task.coding", scope="project", scope_id="ananta",
                mode="profile", profile_id="local-heavy", fallback_group_id="code",
            ),
        ),
        "fallback_groups": (ModelFallbackGroup(
            group_id="code",
            candidates=(
                ModelFallbackCandidate(profile_id="local-heavy"),
                ModelFallbackCandidate(profile_id="local-fast"),
            ),
        ),),
    })
    route = _effective_service(configuration).dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
        project_id="ananta",
        requires_tools=True,
        approximate_context_tokens=1000,
    ))
    assert route.assignment_source == "project:ananta"
    assert route.resolved_profile_id == "local-heavy"
    assert route.candidate_profile_ids == ("local-heavy", "local-fast")
    assert route.executable is True
    assert any(item.source == "request_runtime_override" for item in route.decisions)


def test_dry_run_can_validate_unpersisted_configuration_without_mutation():
    persisted = _service().read().model_copy(update={
        "revision": 2,
        "assignments": (ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-fast",
        ),),
    })
    draft = persisted.model_copy(update={
        "assignments": (ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-heavy",
        ),),
    })
    service = _effective_service(persisted)
    route = service.dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
        configuration=draft,
    ))

    assert route.resolved_profile_id == "local-heavy"
    assert route.configuration_revision == 2
    persisted_route = service.dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
    ))
    assert persisted_route.resolved_profile_id == "local-fast"


def test_dry_run_disabled_assignment_is_terminal():
    configuration = _service().read().model_copy(update={
        "assignments": (ModelAssignment(
            consumer_id="task.coding", scope="global", mode="disabled",
        ),),
    })
    route = _effective_service(configuration).dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
    ))
    assert route.executable is False
    assert route.assignment_mode == "disabled"
    assert route.decisions[0].reason == "model_routing_consumer_disabled"


def test_dry_run_stops_fallback_chain_at_cloud_policy_block():
    configuration = _service().read().model_copy(update={
        "assignments": (ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-heavy", fallback_group_id="code",
        ),),
        "fallback_groups": (ModelFallbackGroup(
            group_id="code",
            candidates=(
                ModelFallbackCandidate(profile_id="local-heavy"),
                ModelFallbackCandidate(profile_id="cloud-code", cloud_allowed=True),
                ModelFallbackCandidate(profile_id="local-fast"),
            ),
        ),),
    })
    route = _effective_service(configuration).dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
        contains_secrets=True,
        allow_cloud=True,
    ))
    assert route.candidate_profile_ids == ("local-heavy", "local-fast")
    assert any(
        profile_id == "cloud-code" and "secrets" in reason
        for profile_id, reason in route.blocked_candidates
    )


def test_dry_run_inherits_primary_and_overrides_fallback_at_narrow_scope():
    configuration = _service().read().model_copy(update={
        "assignments": (
            ModelAssignment(
                consumer_id="task.coding", scope="global", mode="profile",
                profile_id="local-fast",
            ),
            ModelAssignment(
                consumer_id="task.coding", scope="project", scope_id="ananta",
                mode="inherit", fallback_group_id="project-code",
            ),
        ),
        "fallback_groups": (ModelFallbackGroup(
            group_id="project-code",
            candidates=(ModelFallbackCandidate(profile_id="local-heavy"),),
        ),),
    })
    route = _effective_service(configuration).dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
        project_id="ananta",
    ))
    assert route.assignment_source == "global"
    assert route.inheritance_sources == ("project:ananta",)
    assert route.fallback_group_id == "project-code"
    assert route.resolved_profile_id == "local-fast"
    assert route.candidate_profile_ids == ("local-fast", "local-heavy")


def test_dry_run_data_class_blocks_cloud_and_keeps_explicit_local_route():
    configuration = _service().read().model_copy(update={
        "assignments": (ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="cloud-code", fallback_group_id="safe-code",
        ),),
        "fallback_groups": (ModelFallbackGroup(
            group_id="safe-code",
            candidates=(
                ModelFallbackCandidate(profile_id="cloud-code", cloud_allowed=True),
                ModelFallbackCandidate(profile_id="local-heavy"),
            ),
        ),),
    })
    route = _effective_service(configuration).dry_run(ModelRoutingDryRunCommand(
        consumer_id="task.coding",
        data_class="confidential",
        allow_cloud=True,
    ))
    assert route.resolved_profile_id == "local-heavy"
    assert route.candidate_profile_ids == ("local-heavy",)
    assert any(
        profile_id == "cloud-code" and "data_policy_blocked" in reason
        for profile_id, reason in route.blocked_candidates
    )
