from __future__ import annotations

from agent.providers.interfaces import ProviderDescriptor
from agent.providers.registry import GenericProviderRegistry


def test_optional_provider_dependencies_do_not_break_core_import_path() -> None:
    import agent.services.task_delegation_services as task_delegation_services

    assert task_delegation_services.WorkerExecutionContextFactory is not None


def test_registry_keeps_optional_providers_disabled_or_degraded_without_startup_failure() -> None:
    registry = GenericProviderRegistry()
    registry.register_descriptor(
        ProviderDescriptor(
            provider_id="workflow_optional",
            provider_family="workflow",
            capabilities=("execute",),
            risk_class="medium",
            enabled_by_default=False,
        )
    )
    registry.register_descriptor(
        ProviderDescriptor(
            provider_id="domain_graph_optional",
            provider_family="domain_graph",
            capabilities=("ingest",),
            risk_class="medium",
            enabled_by_default=False,
        )
    )
    registry.register_descriptor(
        ProviderDescriptor(
            provider_id="worker_optional",
            provider_family="worker_execution",
            capabilities=("worker_job_execute",),
            risk_class="high",
            enabled_by_default=True,
        )
    )

    def _missing_dep_factory():
        raise ModuleNotFoundError("optional-provider-sdk")

    registry.register_factory(
        provider_family="worker_execution",
        provider_id="worker_optional",
        factory=_missing_dep_factory,
    )

    workflow_resolution = registry.resolve_provider(provider_family="workflow", provider_id="workflow_optional")
    domain_resolution = registry.resolve_provider(provider_family="domain_graph", provider_id="domain_graph_optional")
    worker_resolution = registry.resolve_provider(provider_family="worker_execution", provider_id="worker_optional")

    assert workflow_resolution.status == "disabled"
    assert domain_resolution.status == "disabled"
    assert worker_resolution.status == "degraded"
    assert "missing_optional_dependency" in str(worker_resolution.reason or "")
