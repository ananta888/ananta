from __future__ import annotations

import pytest

from agent.services.openrouter_model_inventory_adapter import (
    OpenRouterModelInventoryAdapter,
    RequestsOpenRouterModelsClient,
)


class _Client:
    def __init__(self, rows=()) -> None:
        self.rows = tuple(rows)
        self.keys: list[str] = []

    def list_models(self, *, api_key: str):
        self.keys.append(api_key)
        return self.rows


def test_openrouter_inventory_normalizes_capabilities_cost_and_evidence():
    client = _Client(({
        "id": "vendor/coder:free",
        "canonical_slug": "vendor/coder",
        "name": "Coder",
        "context_length": 131072,
        "architecture": {
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
            "modality": "text+image->text",
            "tokenizer": "Test",
        },
        "supported_parameters": ["tools", "structured_outputs", "reasoning"],
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    },))
    adapter = OpenRouterModelInventoryAdapter(lambda: "server-secret", client)

    snapshot = adapter.collect()

    assert client.keys == ["server-secret"]
    model = snapshot.models[0]
    assert model.model_id == "vendor/coder:free"
    assert model.context_window == 131072
    assert model.input_modalities == ("image", "text")
    assert model.price_input_per_million == 1.0
    assert model.price_output_per_million == 2.0
    claims = {item.capability_id: item for item in model.capabilities}
    assert claims["tools"].value == "supported"
    assert claims["vision"].value == "supported"
    assert claims["tools"].source_id == "openrouter.models"
    assert {item.fact_id for item in model.metadata_facts} >= {
        "canonical_slug", "modality", "tokenizer",
    }
    assert "server-secret" not in model.model_dump_json()


def test_openrouter_inventory_fails_with_bounded_reason_when_key_is_missing():
    adapter = OpenRouterModelInventoryAdapter(lambda: "", _Client())

    with pytest.raises(
        RuntimeError, match="provider_openrouter_credentials_unavailable"
    ):
        adapter.collect()


def test_openrouter_inventory_skips_rows_without_stable_model_id():
    adapter = OpenRouterModelInventoryAdapter(
        lambda: "key", _Client(({}, {"id": "vendor/model", "name": "Model"}))
    )

    assert [item.model_id for item in adapter.collect().models] == ["vendor/model"]


def test_http_client_uses_fixed_endpoint_timeout_and_redacts_credential(monkeypatch):
    captured = {}

    class Response:
        status_code = 401

        @staticmethod
        def iter_content(*, chunk_size):
            return iter(())

    def get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("requests.get", get)
    client = RequestsOpenRouterModelsClient(timeout_seconds=999)

    with pytest.raises(RuntimeError, match="provider_openrouter_http_401") as error:
        client.list_models(api_key="secret-value")

    assert captured["url"] == "https://openrouter.ai/api/v1/models"
    assert captured["params"] == {"output_modalities": "all"}
    assert captured["timeout"] == 20.0
    assert captured["stream"] is True
    assert "secret-value" not in str(error.value)
