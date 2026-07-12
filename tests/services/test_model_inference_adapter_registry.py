from __future__ import annotations

from agent.services.model_inference_adapter_registry import ModelInferenceAdapterRegistry
from agent.services.restricted_inference_config_service import RestrictedInferenceModelConfig


def test_registry_always_builds_mock_adapter() -> None:
    registry = ModelInferenceAdapterRegistry()
    adapter = registry.build(RestrictedInferenceModelConfig(id="mock", engine="mock"))

    status = adapter.status()
    assert status.status == "ready"
    assert status.engine == "mock"


def test_registry_reports_unknown_engine_unavailable() -> None:
    registry = ModelInferenceAdapterRegistry()
    statuses = registry.statuses([RestrictedInferenceModelConfig(id="bad", engine="missing-engine")])

    assert statuses[0].status == "unavailable"
    assert statuses[0].error == "unknown_engine"


def test_registry_capabilities_are_machine_readable() -> None:
    registry = ModelInferenceAdapterRegistry()
    capabilities = registry.capabilities()

    assert "mock" in capabilities
    assert "rerank" in capabilities["mock"]


def test_status_query_does_not_construct_or_load_real_adapter() -> None:
    registry = ModelInferenceAdapterRegistry()
    registry.register(
        "fixture-engine",
        lambda _model: (_ for _ in ()).throw(AssertionError("status must not construct adapters")),
        frozenset({"classification"}),
    )

    statuses = registry.statuses(
        [RestrictedInferenceModelConfig(id="fixture", engine="fixture-engine", local_path="/not-loaded")]
    )

    assert statuses[0].status == "declared"
    assert statuses[0].capabilities == frozenset({"classification"})
