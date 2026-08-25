from __future__ import annotations

import pytest

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_routing_legacy_migration_service import (
    ModelRoutingLegacyMigrationError,
    ModelRoutingLegacyMigrationService,
)
from agent.services.model_selection_service import (
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
)
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelRoutingConfiguration,
    ModelRoutingLegacyMigrationApplyCommand,
)


def _profile(profile_id: str, provider: str, model: str) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id=provider,
        model=model,
        model_role="any",
        local=provider not in {"openai", "openrouter"},
    )


def _service(*, configuration=None, config=None, profiles=None):
    profiles = tuple(profiles or (_profile("local-chat", "lmstudio", "lfm2.5"),))
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(
            configuration or ModelRoutingConfiguration(revision=0)
        ),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=(item.profile_id for item in profiles),
        known_models=((item.provider_id, item.model) for item in profiles),
    )
    service = ModelRoutingLegacyMigrationService(
        assignments=assignments,
        profiles=profiles,
        legacy_config=config or {
            "default_provider": "lmstudio",
            "default_model": "lfm2.5",
            "llm_config": {},
            "hub_copilot": {},
        },
    )
    return service, assignments


def test_preview_maps_exact_profiles_and_apply_is_idempotent():
    service, assignments = _service()
    preview = service.preview()

    assert preview.applicable is True
    assert {item.status for item in preview.entries} == {"proposed"}
    assert len(preview.proposed_configuration.assignments) == 4

    command = ModelRoutingLegacyMigrationApplyCommand(
        expected_revision=preview.current_revision,
        confirmation_digest=preview.confirmation_digest,
    )
    applied = service.apply(command)
    assert applied.revision == 1

    second = service.preview()
    assert {item.status for item in second.entries} == {"preserved"}
    unchanged = service.apply(ModelRoutingLegacyMigrationApplyCommand(
        expected_revision=second.current_revision,
        confirmation_digest=second.confirmation_digest,
    ))
    assert unchanged.revision == 1
    assert assignments.read() == unchanged


def test_existing_central_assignment_is_preserved_and_shadow_mismatch_blocks_gate():
    service, _ = _service(configuration=ModelRoutingConfiguration(
        revision=3,
        assignments=(ModelAssignment(
            consumer_id="chat.ai_snake", scope="global", mode="profile",
            profile_id="other-chat",
        ),),
    ), profiles=(
        _profile("local-chat", "lmstudio", "lfm2.5"),
        _profile("other-chat", "lmstudio", "other"),
    ))

    preview = service.preview()
    snake = next(item for item in preview.entries if item.consumer_id == "chat.ai_snake")
    assert snake.status == "preserved"
    assert snake.matched_profile_id == "other-chat"
    assert service.shadow_report().matches is False
    assert service.release_gate().ready is False


def test_unresolved_legacy_identity_prevents_partial_apply():
    service, assignments = _service(config={
        "default_provider": "lmstudio",
        "default_model": "not-registered",
    })
    preview = service.preview()

    assert preview.applicable is False
    assert {item.status for item in preview.entries} == {"unresolved"}
    with pytest.raises(ModelRoutingLegacyMigrationError, match="not_applicable"):
        service.apply(ModelRoutingLegacyMigrationApplyCommand(
            expected_revision=preview.current_revision,
            confirmation_digest=preview.confirmation_digest,
        ))
    assert assignments.read().revision == 0


def test_confirmation_digest_and_revision_are_both_required():
    service, _ = _service()
    preview = service.preview()

    with pytest.raises(ModelRoutingLegacyMigrationError, match="confirmation_invalid"):
        service.apply(ModelRoutingLegacyMigrationApplyCommand(
            expected_revision=preview.current_revision,
            confirmation_digest="sha256:" + "0" * 64,
        ))
    with pytest.raises(ModelRoutingLegacyMigrationError, match="revision_conflict"):
        service.apply(ModelRoutingLegacyMigrationApplyCommand(
            expected_revision=99,
            confirmation_digest=preview.confirmation_digest,
        ))
