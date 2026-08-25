"""Bounded server-side OpenRouter model inventory adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from agent.services.model_inventory_service import ModelInventorySnapshot
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelMetadataFact,
    ModelRuntime,
    ModelSourceKind,
)

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_MODELS = 10_000


class OpenRouterModelsClientPort(Protocol):
    def list_models(self, *, api_key: str) -> tuple[Mapping[str, Any], ...]: ...


class RequestsOpenRouterModelsClient:
    """HTTP adapter with a fixed target, hard timeout and response limit."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self._timeout_seconds = max(0.5, min(float(timeout_seconds), 20.0))

    def list_models(self, *, api_key: str) -> tuple[Mapping[str, Any], ...]:
        import requests

        try:
            response = requests.get(
                _MODELS_URL,
                params={"output_modalities": "all"},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "User-Agent": "Ananta-ModelInventory/1",
                },
                timeout=self._timeout_seconds,
                stream=True,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"provider_openrouter_http_{int(response.status_code)}"
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise RuntimeError("provider_openrouter_response_too_large")
                chunks.append(chunk)
            payload = response.json() if not chunks else json.loads(
                b"".join(chunks).decode("utf-8")
            )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"provider_openrouter_request_failed:{type(exc).__name__}"
            ) from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            raise RuntimeError("provider_openrouter_payload_invalid")
        if len(data) > _MAX_MODELS:
            raise RuntimeError("provider_openrouter_model_limit_exceeded")
        return tuple(item for item in data if isinstance(item, Mapping))


class OpenRouterModelInventoryAdapter:
    source_id = "openrouter.models"
    source_kind = ModelSourceKind.DISCOVERED
    cache_ttl_seconds = 300.0
    stale_after_seconds = 1800.0

    def __init__(
        self,
        api_key_loader: Callable[[], str],
        client: OpenRouterModelsClientPort | None = None,
    ) -> None:
        self._api_key_loader = api_key_loader
        self._client = client or RequestsOpenRouterModelsClient()

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        api_key = str(self._api_key_loader() or "").strip()
        if not api_key:
            raise RuntimeError("provider_openrouter_credentials_unavailable")
        rows = self._client.list_models(api_key=api_key)
        return ModelInventorySnapshot(models=tuple(
            descriptor for row in rows
            if (descriptor := self._descriptor(row)) is not None
        ))

    def _descriptor(
        self, row: Mapping[str, Any]
    ) -> ModelInventoryDescriptor | None:
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            return None
        architecture = row.get("architecture")
        architecture = architecture if isinstance(architecture, Mapping) else {}
        supported = {
            str(value or "").strip().lower()
            for value in (row.get("supported_parameters") or ())
        }
        inputs = self._modalities(architecture.get("input_modalities"))
        outputs = self._modalities(architecture.get("output_modalities"))
        capabilities = {
            "tools": "tools" in supported,
            "json": bool(supported & {"response_format", "structured_outputs"}),
            "reasoning": bool(supported & {"reasoning", "include_reasoning"}),
            "vision": "image" in inputs,
            "audio": "audio" in inputs or "audio" in outputs,
            "embeddings": "embeddings" in outputs,
        }
        facts = []
        for fact_id, value in (
            ("canonical_slug", row.get("canonical_slug")),
            ("instruct_type", architecture.get("instruct_type")),
            ("tokenizer", architecture.get("tokenizer")),
            ("modality", architecture.get("modality")),
            ("knowledge_cutoff", row.get("knowledge_cutoff")),
        ):
            normalized = str(value or "").strip()
            if normalized:
                facts.append(ModelMetadataFact(
                    fact_id=fact_id,
                    value=normalized[:512],
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                ))
        pricing = row.get("pricing")
        pricing = pricing if isinstance(pricing, Mapping) else {}
        return ModelInventoryDescriptor(
            provider_id="openrouter",
            model_id=model_id,
            executor_id="api:openrouter",
            display_name=str(row.get("name") or model_id)[:512],
            runtime=ModelRuntime.CLOUD,
            source_ids=(self.source_id,),
            source_kinds=(self.source_kind,),
            availability=ModelAvailability.AVAILABLE,
            health=ModelHealth.HEALTHY,
            listing_supported=True,
            auth_mode="api_key",
            auth_ready=True,
            context_window=self._positive_int(row.get("context_length")),
            input_modalities=inputs,
            output_modalities=outputs,
            price_input_per_million=self._per_million(pricing.get("prompt")),
            price_output_per_million=self._per_million(pricing.get("completion")),
            capabilities=tuple(ModelCapabilityClaim(
                capability_id=capability,
                value="supported" if enabled else "unknown",
                evidence=ModelMetadataEvidence.DETECTED,
                source_id=self.source_id,
            ) for capability, enabled in sorted(capabilities.items())),
            metadata_facts=tuple(facts),
        )

    @staticmethod
    def _modalities(value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            str(item).strip().lower() for item in value
            if str(item or "").strip()
        )

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _per_million(value: Any) -> float | None:
        try:
            result = Decimal(str(value)) * Decimal(1_000_000)
        except (InvalidOperation, TypeError, ValueError):
            return None
        return float(result) if result >= 0 else None


__all__ = [
    "OpenRouterModelInventoryAdapter", "OpenRouterModelsClientPort",
    "RequestsOpenRouterModelsClient",
]
