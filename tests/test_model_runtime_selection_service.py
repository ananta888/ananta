from __future__ import annotations

import pytest

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver
from agent.services.model_runtime_selection_service import (
    HubModelRuntimeSelectionService,
    ModelRuntimeSelectionError,
)
from agent.services.model_selection_service import (
    EffectiveModelRoutingService,
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
)
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelRoutingConfiguration,
    ModelRoutingDryRunCommand,
)


def _service(configuration: ModelRoutingConfiguration):
    profile = ModelProfile(
        profile_id="local-chat",
        provider_id="lmstudio",
        model="lfm2.5",
        model_role="chat",
        local=True,
        base_url="http://mini-pc:1234/v1",
    )
    routing = EffectiveModelRoutingService(
        repository=InMemoryModelRoutingConfigurationRepository(configuration),
        consumers=ModelConsumerRegistry.defaults(),
        resolver=ModelProfileResolver([profile]),
    )
    return HubModelRuntimeSelectionService(routing)


def test_projects_explicit_hub_assignment_for_runtime_use():
    service = _service(ModelRoutingConfiguration(
        revision=7,
        assignments=(ModelAssignment(
            consumer_id="chat.ai_snake",
            scope="global",
            mode="profile",
            profile_id="local-chat",
        ),),
    ))

    selection = service.resolve_explicit(ModelRoutingDryRunCommand(
        consumer_id="chat.ai_snake",
    ))

    assert selection is not None
    assert selection.configuration_revision == 7
    assert selection.profile_id == "local-chat"
    assert selection.provider_id == "lmstudio"
    assert selection.model_id == "lfm2.5"
    assert selection.base_url == "http://mini-pc:1234/v1"


def test_preserves_legacy_fallback_when_no_explicit_assignment_exists():
    service = _service(ModelRoutingConfiguration(revision=0))

    assert service.resolve_explicit(ModelRoutingDryRunCommand(
        consumer_id="chat.ai_snake",
    )) is None


def test_explicit_disabled_consumer_fails_closed():
    service = _service(ModelRoutingConfiguration(
        revision=1,
        assignments=(ModelAssignment(
            consumer_id="chat.ai_snake", scope="global", mode="disabled",
        ),),
    ))

    with pytest.raises(ModelRuntimeSelectionError, match="not_executable"):
        service.resolve_explicit(ModelRoutingDryRunCommand(
            consumer_id="chat.ai_snake",
        ))
