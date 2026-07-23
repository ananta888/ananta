from __future__ import annotations

from typing import Any, Mapping

import pytest
from pydantic import ValidationError

from agent.services.model_catalog_service import (
    CatalogQuery,
    ModelCatalogCapabilityPolicy,
    ModelCatalogService,
    ModelDefaultSelectionError,
    ModelDefaultSelectionService,
    ProviderDiscovery,
)
from ananta_contracts.model_catalog import (
    MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
    ModelDefaultSelectionCommand,
    ModelSummary,
)


class _Inventory:
    def list_specs(self, query):
        return [
            {
                "provider": "broken",
                "display_name": "Broken",
                "base_url": "http://secret.invalid",
                "available": True,
                "models": ["fallback"],
                "capabilities": {"dynamic_models": True},
            },
            {
                "provider": "openai",
                "display_name": "OpenAI",
                "base_url": "https://api.invalid",
                "available": True,
                "models": ["gpt-safe"],
                "capabilities": {
                    "dynamic_models": False,
                    "supports_chat": True,
                },
            },
        ]

    def discover(self, provider, query):
        raise RuntimeError("credential=must-not-leak")

    def voice_entry(self):
        return {
            "provider": "voice",
            "base_url": "http://voice.invalid",
            "available": False,
            "models": [],
            "capabilities": {
                "status": "degraded",
                "status_reason": "voice.runtime_unavailable",
            },
        }


class _Policy:
    def benchmark_rows(self, task_kind):
        return (), {}

    def routing_decision(self, provider_entry, task_kind):
        return {
            "provider": provider_entry["provider"],
            "eligible_for_inference": bool(provider_entry["available"]),
        }

    def fallback_policy(self):
        return {"order": []}


class _Store:
    def __init__(self):
        self.value = None

    def save(self, *, provider_id: str, model_id: str):
        self.value = (provider_id, model_id)


class _Runtime(_Store):
    def apply(self, *, provider_id: str, model_id: str):
        self.value = (provider_id, model_id)


def _service() -> ModelCatalogService:
    return ModelCatalogService(inventory=_Inventory(), policy=_Policy())


def test_provider_failure_isolated_and_versioned_catalog_has_no_secrets():
    catalog = _service().versioned_catalog(
        CatalogQuery(default_provider="openai", default_model="gpt-safe")
    )
    payload = catalog.to_wire()

    assert {(item["provider_id"], item["model_id"]) for item in payload["models"]} == {
        ("broken", "fallback"),
        ("openai", "gpt-safe"),
    }
    assert payload["provider_failures"] == [
        {
            "provider_id": "broken",
            "reason_code": "provider_model_discovery_failed",
        },
        {
            "provider_id": "voice",
            "reason_code": "voice_provider_unavailable",
        },
    ]
    serialized = str(payload).lower()
    assert "base_url" not in serialized
    assert "credential" not in serialized
    assert "secret" not in serialized


def test_model_summary_contract_rejects_sensitive_or_unknown_fields():
    with pytest.raises(ValidationError):
        ModelSummary.model_validate(
            {
                "provider_id": "openai",
                "runtime": "cloud",
                "model_id": "gpt-safe",
                "display_name": "Safe",
                "availability": "available",
                "health": "healthy",
                "api_key": "forbidden",
            }
        )


def test_default_selection_is_allowlisted_and_available():
    store = _Store()
    runtime = _Runtime()
    selector = ModelDefaultSelectionService(
        catalog=_service(),
        store=store,
        runtime=runtime,
    )
    selected = selector.select(
        ModelDefaultSelectionCommand(
            schema=MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            provider_id="openai",
            model_id="gpt-safe",
        ),
        query=CatalogQuery(
            default_provider="openai",
            default_model="gpt-safe",
        ),
    )

    assert (selected.provider_id, selected.model_id) == (
        "openai",
        "gpt-safe",
    )
    assert store.value == runtime.value == ("openai", "gpt-safe")

    with pytest.raises(
        ModelDefaultSelectionError,
        match="model_default_selection_not_allowlisted",
    ):
        selector.select(
            ModelDefaultSelectionCommand(
                schema=MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
                provider_id="openai",
                model_id="arbitrary",
            ),
            query=CatalogQuery(
                default_provider="openai",
                default_model="gpt-safe",
            ),
        )


def test_model_catalog_capability_policy_denies_auth_disabled_and_unknown_users():
    policy = ModelCatalogCapabilityPolicy()

    assert policy.allows(
        "model_catalog.refresh",
        is_admin=True,
        claims={},
    )
    assert not policy.allows(
        "model_catalog.refresh",
        is_admin=True,
        claims={"auth_mode": "auth_disabled"},
    )
    assert policy.allows(
        "model_catalog.refresh",
        is_admin=False,
        claims={"capabilities": ["model_catalog.refresh"]},
    )
    assert not policy.allows(
        "model_catalog.refresh",
        is_admin=False,
        claims={},
    )
