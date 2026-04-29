from __future__ import annotations

from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport
from agent.providers.registry import GenericProviderRegistry


class _MockRuntime:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(status="healthy")


def test_registry_reports_unknown_provider_as_unavailable() -> None:
    registry = GenericProviderRegistry()
    resolution = registry.resolve_provider(provider_family="workflow", provider_id="missing")
    assert resolution.status == "unknown"
    assert resolution.reason == "provider_not_registered"


def test_registry_keeps_provider_disabled_by_default() -> None:
    registry = GenericProviderRegistry()
    descriptor = ProviderDescriptor(
        provider_id="mock_workflow",
        provider_family="workflow",
        capabilities=("dry_run",),
        risk_class="low",
        enabled_by_default=False,
    )
    registry.register_provider(descriptor=descriptor, factory=lambda: _MockRuntime(descriptor))

    resolution = registry.resolve_provider(provider_family="workflow", provider_id="mock_workflow")
    assert resolution.status == "disabled"
    assert resolution.reason == "provider_disabled_by_default"


def test_registry_reports_missing_optional_dependency_as_degraded() -> None:
    registry = GenericProviderRegistry()
    descriptor = ProviderDescriptor(
        provider_id="missing_dep",
        provider_family="workflow",
        capabilities=("execute",),
        risk_class="medium",
        enabled_by_default=True,
    )
    def _factory() -> _MockRuntime:
        raise ModuleNotFoundError("n8n")

    registry.register_provider(descriptor=descriptor, factory=_factory)

    resolution = registry.resolve_provider(provider_family="workflow", provider_id="missing_dep")
    assert resolution.status == "degraded"
    assert str(resolution.reason or "").startswith("missing_optional_dependency:")


def test_registry_resolves_enabled_mock_provider() -> None:
    registry = GenericProviderRegistry()
    descriptor = ProviderDescriptor(
        provider_id="mock_domain_graph",
        provider_family="domain_graph",
        capabilities=("ingest",),
        risk_class="low",
        enabled_by_default=False,
    )
    registry.register_provider(descriptor=descriptor, factory=lambda: _MockRuntime(descriptor))

    resolution = registry.resolve_provider(
        provider_family="domain_graph",
        provider_id="mock_domain_graph",
        enable=True,
    )
    assert resolution.status == "available"
    assert resolution.provider is not None
    assert resolution.provider.descriptor.provider_id == "mock_domain_graph"
