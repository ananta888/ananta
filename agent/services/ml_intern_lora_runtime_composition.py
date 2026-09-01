"""Shared Hub composition for LoRA runtime lifecycle management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_lora_inference_service import get_lora_inference_service, resolve_lora_storage_config
from agent.services.ml_intern_lora_runtime_management_service import MlInternLoraRuntimeManagementService
from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config
from agent.services.unsloth_runtime_handoff_composition import runtime_endpoint_registry_from_config
from agent.services.unsloth_storage_governance_service import storage_catalog_from_config


def lora_runtime_management_service_from_config(
    agent_config: Mapping[str, Any] | None,
) -> MlInternLoraRuntimeManagementService:
    """Build the Hub lifecycle service without coupling domain routes to each other."""

    config = dict(agent_config or {})
    storage = resolve_lora_storage_config(config)
    training_config = normalize_ml_intern_training_config(dict(config.get("ml_intern_training") or {}))
    return MlInternLoraRuntimeManagementService(
        registry=MlInternAdapterRegistryService(storage["registry_path"]),
        inference=get_lora_inference_service(),
        endpoint_registry=runtime_endpoint_registry_from_config(
            config,
            storage_references=storage_catalog_from_config(training_config),
        ),
    )


__all__ = ["lora_runtime_management_service_from_config"]
