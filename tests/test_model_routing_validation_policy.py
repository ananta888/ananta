from __future__ import annotations

import pytest

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_routing_validation_policy import (
    ModelRoutingValidationPolicy,
)
from agent.services.model_selection_service import (
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
)
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingMutationCommand,
)


def _profiles() -> tuple[ModelProfile, ...]:
    return (
        ModelProfile(
            profile_id="local-chat", provider_id="lmstudio", model="chat",
            model_role="chat", local=True, supports_json=False,
            supports_tools=False, tool_calling_mode="none", context_tokens=8192,
        ),
        ModelProfile(
            profile_id="local-code", provider_id="lmstudio", model="code",
            model_role="coder", local=True, supports_json=True,
            supports_tools=True, tool_calling_mode="native_tools",
            context_tokens=32768,
        ),
        ModelProfile(
            profile_id="cloud-code", provider_id="openrouter", model="cloud",
            model_role="coder", cloud=True, cloud_allowed=True,
            block_secret_context=True, supports_json=True,
        ),
    )


def _service() -> ModelRoutingAssignmentService:
    profiles = _profiles()
    consumers = ModelConsumerRegistry.defaults()
    return ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=consumers,
        known_profile_ids=(profile.profile_id for profile in profiles),
        known_models=((profile.provider_id, profile.model) for profile in profiles),
        validation_policy=ModelRoutingValidationPolicy(
            consumers=consumers, profiles=profiles
        ),
    )


def _command(*, assignment: ModelAssignment, group=None):
    return ModelRoutingMutationCommand(
        schema="ananta.model-routing-mutation-command.v1",
        expected_revision=0,
        assignments=(assignment,),
        fallback_groups=(group,) if group else (),
    )


def test_direct_assignment_rejects_hard_consumer_capability_mismatch():
    command = _command(assignment=ModelAssignment(
        consumer_id="task.coding", scope="global", mode="profile",
        profile_id="local-chat",
    ))

    issues = _service().validation_issues(command)

    assert [(item.reason_code, item.reference) for item in issues] == [(
        "model_profile_capability_mismatch:code",
        "task.coding@global:global",
    )]
    with pytest.raises(ValueError, match="model_profile_capability_mismatch:code"):
        _service().apply(command)


def test_fallback_rejects_cloud_bypass_and_candidate_requirement_mismatch():
    group = ModelFallbackGroup(
        group_id="guarded",
        candidates=(
            ModelFallbackCandidate(
                profile_id="local-chat", requires_tools=True,
            ),
            ModelFallbackCandidate(
                profile_id="cloud-code", cloud_allowed=False,
            ),
        ),
    )
    command = _command(
        assignment=ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-code", fallback_group_id="guarded",
        ),
        group=group,
    )

    reasons = {item.reason_code for item in _service().validation_issues(command)}

    assert "model_profile_capability_mismatch:tools" in reasons
    assert "model_profile_capability_mismatch:code" in reasons
    assert "model_fallback_cloud_candidate_not_allowed" in reasons


def test_context_metadata_mismatch_is_warning_and_does_not_block_apply():
    group = ModelFallbackGroup(
        group_id="large-context",
        candidates=(ModelFallbackCandidate(
            profile_id="local-code", max_context_tokens=100_000,
        ),),
    )
    command = _command(
        assignment=ModelAssignment(
            consumer_id="task.coding", scope="global", mode="profile",
            profile_id="local-code", fallback_group_id="large-context",
        ),
        group=group,
    )

    issues = _service().validation_issues(command)

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].reason_code == "model_fallback_context_limit_exceeds_profile"
    assert _service().apply(command).revision == 1
