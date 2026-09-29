from __future__ import annotations

import pytest

from agent.providers.interfaces import ProviderDescriptor
from agent.providers.registry import GenericProviderRegistry
from agent.services.model_inference_adapter_registry import ModelInferenceAdapterRegistry
from agent.services.provider_capability_service import (
    GenericProviderRegistryCapabilitySource,
    ModelInferenceRegistryCapabilitySource,
    ProviderCapability,
    ProviderCapabilityService,
    ProviderSelectionRequirement,
    WorkerProviderRegistryCapabilitySource,
)
from worker.core.provider_registry import (
    ProviderDiagnostic,
    ProviderEntry,
    ProviderKind,
    ProviderStatus,
    WorkerProviderRegistry,
)


class StaticSource:
    source_id = "static"

    def __init__(self, rows: list[ProviderCapability]) -> None:
        self.rows = rows

    def list_capabilities(self) -> list[ProviderCapability]:
        return list(self.rows)


def test_generic_registry_is_adapted_without_new_global_registry() -> None:
    registry = GenericProviderRegistry()
    registry.register_descriptor(
        ProviderDescriptor(
            provider_id="local-a",
            provider_family="llm",
            capabilities=("text_generation",),
            enabled_by_default=True,
        )
    )

    rows = ProviderCapabilityService(
        [GenericProviderRegistryCapabilitySource(registry)]
    ).list_capabilities()

    assert [(row.provider_id, row.status) for row in rows] == [("local-a", "declared")]


def test_selection_is_capability_and_locality_driven() -> None:
    service = ProviderCapabilityService(
        [
            StaticSource(
                [
                    ProviderCapability(
                        provider_id="cloud-a",
                        provider_family="llm",
                        source="static",
                        status="available",
                        capabilities=("text_generation", "streaming"),
                        locality="cloud",
                        privacy_class="public",
                        cost_class="metered",
                    ),
                    ProviderCapability(
                        provider_id="local-a",
                        provider_family="llm",
                        source="static",
                        status="ready",
                        capabilities=("text_generation", "streaming"),
                        locality="local",
                        privacy_class="confidential",
                        cost_class="local",
                    ),
                ]
            )
        ]
    )

    decision = service.select(
        ProviderSelectionRequirement(
            required_capabilities=("streaming",),
            allowed_localities=("local", "cloud"),
        )
    )

    assert decision.status == "selected"
    assert decision.selected is not None
    assert decision.selected.provider_id == "local-a"


def test_selection_fails_closed_when_capability_is_missing() -> None:
    service = ProviderCapabilityService(
        [
            StaticSource(
                [
                    ProviderCapability(
                        provider_id="local-a",
                        provider_family="llm",
                        source="static",
                        status="ready",
                        capabilities=("text_generation",),
                        locality="local",
                        privacy_class="confidential",
                    )
                ]
            )
        ]
    )

    decision = service.select(
        ProviderSelectionRequirement(required_capabilities=("tool_calling",))
    )

    assert decision.status == "incompatible"
    assert decision.selected is None
    assert decision.reason_code == "no_compatible_provider"
    assert decision.rejected[0]["reason_code"] == "missing_capabilities"


def test_credential_ref_must_be_opaque_and_safe() -> None:
    try:
        ProviderCapability(
            provider_id="x",
            provider_family="llm",
            source="static",
            status="ready",
            credential_ref="plain-secret",
        )
    except ValueError as exc:
        assert str(exc) == "credential_ref_must_be_opaque_reference"
    else:
        raise AssertionError("plain credential material must not be accepted")


def test_worker_registry_adapter_exports_safe_hub_decision_fields() -> None:
    registry = WorkerProviderRegistry()
    registry.register(
        ProviderEntry(
            id="local-worker",
            kind=ProviderKind.local,
            supports_tools=True,
            supports_streaming=True,
            default_model="model-a",
            credential_source="env:LOCAL_WORKER_KEY",
        )
    )
    registry.record_diagnostic(
        ProviderDiagnostic(
            provider_id="local-worker",
            status=ProviderStatus.available,
            kind=ProviderKind.local,
            latency_ms=42,
        )
    )

    rows = WorkerProviderRegistryCapabilitySource(registry).list_capabilities()

    assert len(rows) == 1
    assert rows[0].capabilities == ("text_generation", "tool_calling", "streaming")
    assert rows[0].credential_ref == "env:LOCAL_WORKER_KEY"
    assert rows[0].latency_class == "fast"
    assert "credential_source" not in rows[0].as_dict()


def test_restricted_inference_registry_is_adapted_through_same_port() -> None:
    rows = ModelInferenceRegistryCapabilitySource(
        ModelInferenceAdapterRegistry()
    ).list_capabilities()

    assert rows
    assert all(row.provider_family == "restricted_inference" for row in rows)
    assert all(row.locality == "local" for row in rows)
    assert any("embeddings" in row.capabilities for row in rows)


def test_capability_payload_rejects_credential_material_in_metadata() -> None:
    with pytest.raises(ValueError, match="sensitive_metadata_denied"):
        ProviderCapability(
            provider_id="unsafe",
            provider_family="llm",
            source="test",
            status="ready",
            metadata={"api_key": "plain-secret"},
        )


def test_native_and_langchain_consumers_receive_the_same_hub_selection() -> None:
    service = ProviderCapabilityService(
        [
            StaticSource(
                [
                    ProviderCapability(
                        provider_id="local-shared",
                        provider_family="llm",
                        source="worker",
                        status="available",
                        capabilities=("text_generation", "streaming"),
                        locality="local",
                        privacy_class="confidential",
                        cost_class="local",
                    )
                ]
            )
        ]
    )
    requirement = ProviderSelectionRequirement(
        required_capabilities=("text_generation",),
        allowed_localities=("local",),
    )

    native_decision = service.select(requirement).as_dict()
    langchain_decision = service.select(requirement).as_dict()

    assert native_decision == langchain_decision
    assert native_decision["selected"]["provider_id"] == "local-shared"
