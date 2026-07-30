from __future__ import annotations

import json

import pytest

from agent.services.model_inference_adapter_registry import ModelInferenceAdapterRegistry
from agent.services.model_inference_adapters import (
    ALL_CAPABILITIES,
    CAP_CHOICE_SCORING,
    AdapterStatus,
    BaseInferenceAdapter,
)
from agent.services.model_inference_adapters.pytorch_adapter import PyTorchAdapter
from agent.services.model_inference_capability_inventory import (
    CapabilityContractError,
    assert_ready_adapter_contract,
    canonical_capability_inventory_json,
)
from agent.services.restricted_inference_config_service import RestrictedInferenceModelConfig


def test_inventory_is_complete_sorted_and_byte_deterministic() -> None:
    registry = ModelInferenceAdapterRegistry()

    first = canonical_capability_inventory_json(registry)
    second = canonical_capability_inventory_json(registry)
    payload = json.loads(first)

    assert first == second
    assert [item["engine"] for item in payload["adapters"]] == registry.engines()
    assert len({item["engine"] for item in payload["adapters"]}) == len(registry.engines())
    assert payload["source_digest"].startswith("sha256:")
    assert "timestamp" not in first.decode("ascii")
    assert "hostname" not in first.decode("ascii")


def test_each_descriptor_covers_every_capability_with_stable_support_reason() -> None:
    registry = ModelInferenceAdapterRegistry()

    for descriptor in registry.descriptors():
        assert {item.name for item in descriptor.capabilities} == set(ALL_CAPABILITIES)
        assert all(item.support in {"supported", "unsupported", "conditional"} for item in descriptor.capabilities)
        assert all(item.reason_code for item in descriptor.capabilities)


def test_pytorch_does_not_advertise_unimplemented_choice_scoring() -> None:
    registry = ModelInferenceAdapterRegistry()

    assert CAP_CHOICE_SCORING not in registry.capabilities()["pytorch"]
    assert PyTorchAdapter.capability_descriptor().capability(CAP_CHOICE_SCORING).support == "unsupported"


def test_builtin_mock_passes_ready_capability_contract() -> None:
    registry = ModelInferenceAdapterRegistry()
    adapter = registry.build(RestrictedInferenceModelConfig(id="mock", engine="mock"))

    report = assert_ready_adapter_contract(adapter)

    assert report.passed is True
    assert report.failures == ()


class _ContradictoryAdapter(BaseInferenceAdapter):
    ENGINE = "contradictory"
    CAPABILITIES = frozenset({CAP_CHOICE_SCORING})

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name=self.ENGINE,
            engine=self.ENGINE,
            status="ready",
            capabilities=self.CAPABILITIES,
        )

    def score_choices(self, prompt: str, choices: list[str]) -> list:
        raise NotImplementedError


def test_contract_probe_rejects_advertised_not_implemented_operation() -> None:
    with pytest.raises(CapabilityContractError, match="advertised_operation_not_implemented"):
        assert_ready_adapter_contract(_ContradictoryAdapter())
