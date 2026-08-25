from agent.services.model_routing_observability_service import (
    ModelRoutingDiagnosticsService,
    ModelRoutingUsageProjection,
)
from agent.services.model_selection_service import ModelConsumerRegistry
from ananta_contracts.model_catalog import (
    ModelCatalogV2,
    ModelInventoryDescriptor,
    ModelInventorySourceStatus,
    ModelSourceKind,
)
from ananta_contracts.model_selection import (
    EffectiveModelRoute,
    ModelAssignment,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingConfiguration,
)


def _route(*, selected: str = "known", executable: bool = True):
    return EffectiveModelRoute(
        configuration_revision=1,
        consumer_id="chat.ai_snake",
        assignment_source="global:global",
        assignment_mode="profile",
        resolved_profile_id=selected if executable else None,
        provider_id="lmstudio" if executable else None,
        model_id="lfm" if executable else None,
        candidate_profile_ids=("primary", "known"),
        executable=executable,
    )


def test_usage_projection_is_aggregated_and_content_free():
    usage = ModelRoutingUsageProjection()
    usage.record(_route())
    usage.record(_route())

    result = usage.read()[0]

    assert result.selections_total == 2
    assert result.fallback_selections_total == 2
    assert not hasattr(result, "prompt")
    assert not hasattr(result, "tokens")


def test_diagnostics_exposes_orphans_source_failures_and_non_executable_routes():
    configuration = ModelRoutingConfiguration(
        revision=1,
        assignments=(ModelAssignment(
            consumer_id="chat.ai_snake", scope="global", mode="profile",
            profile_id="missing-profile",
        ),),
        fallback_groups=(ModelFallbackGroup(
            group_id="fallbacks",
            candidates=(ModelFallbackCandidate(profile_id="missing-fallback"),),
        ),),
    )
    catalog = ModelCatalogV2(
        catalog_revision=3,
        models=(ModelInventoryDescriptor(
            provider_id="lmstudio", model_id="lfm", executor_id="api:lmstudio",
            display_name="LFM",
        ),),
        sources=(ModelInventorySourceStatus(
            source_id="providers.catalog",
            source_kind=ModelSourceKind.DISCOVERED,
            status="stale",
            stale=True,
            reason_code="provider_timeout",
        ),),
        partial=True,
    )

    result = ModelRoutingDiagnosticsService().build(
        configuration=configuration,
        catalog=catalog,
        consumers=ModelConsumerRegistry.defaults().all(),
        effective_routes=(_route(executable=False),),
        known_profile_ids=("known",),
        usage=(),
    )

    reasons = {item.reason_code for item in result.issues}
    assert result.unresolved_assignment_count == 2
    assert result.non_executable_route_count == 1
    assert {
        "profile_unresolved", "fallback_profile_unresolved",
        "inventory_source_stale", "effective_route_not_executable",
    } <= reasons
    assert result.contains_secrets is False
