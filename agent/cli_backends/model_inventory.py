"""Secret-free, non-executing model inventory adapters for LLM CLIs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from agent.cli_backends.context import default_context as _ctx
from agent.cli_backends.routing import (
    CLI_BACKEND_CAPABILITIES,
    get_cli_backend_runtime_status,
)
from agent.config import settings
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelRuntime,
    ModelSourceKind,
)

INVENTORIED_CLI_BACKENDS = (
    "codex",
    "claude_code",
    "opencode",
    "aider",
    "mistral_code",
    "qwen_code",
    "gemini_cli",
    "copilot_cli",
    "cline",
    "kilo_code",
)


class ModelInventorySnapshotPort(Protocol):
    """Structural result contract consumed by the inventory aggregator."""

    models: tuple[ModelInventoryDescriptor, ...]
    degraded_reason_code: str | None


class CliRuntimeStatusCache:
    """Coalesces the existing non-executing runtime-status projection."""

    def __init__(
        self,
        loader: Callable[[], Mapping[str, Mapping]] = get_cli_backend_runtime_status,
        *,
        ttl_seconds: float = 5.0,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._value: Mapping[str, Mapping] = {}

    def load(self) -> Mapping[str, Mapping]:
        now = time.monotonic()
        with self._lock:
            if self._value and now - self._loaded_at < self._ttl_seconds:
                return self._value
            self._value = self._loader()
            self._loaded_at = time.monotonic()
            return self._value


class CliBackendModelInventoryAdapter:
    source_kind = ModelSourceKind.CONFIGURED
    cache_ttl_seconds = 30.0
    stale_after_seconds = 300.0

    def __init__(self, backend_id: str, status_cache: CliRuntimeStatusCache) -> None:
        if backend_id not in INVENTORIED_CLI_BACKENDS:
            raise ValueError("cli_model_inventory_backend_unsupported")
        self.backend_id = backend_id
        self.source_id = f"cli:{backend_id}"
        self._status_cache = status_cache

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshotPort:
        status = dict(self._status_cache.load().get(self.backend_id) or {})
        model_id = self._default_model() or "cli-default"
        installed = bool(status.get("binary_available"))
        last_success = bool(status.get("last_success_at"))
        failures = int(status.get("consecutive_failures") or 0)
        availability = (
            ModelAvailability.AVAILABLE
            if last_success and not failures
            else ModelAvailability.DEGRADED
            if installed and failures
            else ModelAvailability.UNKNOWN
            if installed
            else ModelAvailability.UNAVAILABLE
        )
        health = (
            ModelHealth.HEALTHY
            if last_success and not failures
            else ModelHealth.DEGRADED
            if installed and failures
            else ModelHealth.UNKNOWN
            if installed
            else ModelHealth.UNAVAILABLE
        )
        auth_mode = str(status.get("auth_mode") or "").strip() or None
        diagnostics = {str(value or "").strip() for value in (status.get("diagnostics") or ())}
        auth_ready = False if any("missing_api_key" in value for value in diagnostics) else None
        display_name = str((CLI_BACKEND_CAPABILITIES.get(self.backend_id) or {}).get("display_name") or self.backend_id)
        descriptor = ModelInventoryDescriptor(
            provider_id=self.backend_id,
            model_id=model_id,
            executor_id=f"cli:{self.backend_id}",
            display_name=f"{display_name} ({model_id})",
            runtime=self._runtime(status),
            source_ids=(self.source_id,),
            source_kinds=(
                (self.source_kind, ModelSourceKind.OBSERVED_RUNTIME) if last_success else (self.source_kind,)
            ),
            availability=availability,
            health=health,
            configured=self._default_model() is not None,
            installed=installed,
            listing_supported=False,
            auth_mode=auth_mode,
            auth_ready=auth_ready,
            capabilities=(
                ModelCapabilityClaim(
                    capability_id="code",
                    value="supported",
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
                ModelCapabilityClaim(
                    capability_id="model_listing",
                    value="unsupported",
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
            ),
        )
        return _ctx.model_inventory_snapshot_factory(models=(descriptor,))

    def _default_model(self) -> str | None:
        attribute = {
            "codex": "codex_default_model",
            "claude_code": "claude_default_model",
            "opencode": "opencode_default_model",
            "aider": "aider_default_model",
            "mistral_code": "mistral_code_default_model",
        }.get(self.backend_id)
        if attribute is None:
            return None
        return str(getattr(settings, attribute, None) or "").strip() or None

    def _runtime(self, status: Mapping) -> ModelRuntime:
        if self.backend_id == "claude_code":
            return ModelRuntime.CLOUD
        if status.get("target_is_local") is True:
            return ModelRuntime.LOCAL
        target_kind = str(status.get("target_kind") or "").lower()
        if "remote" in target_kind:
            return ModelRuntime.REMOTE
        return ModelRuntime.UNKNOWN


def build_cli_model_inventory_adapters() -> tuple[CliBackendModelInventoryAdapter, ...]:
    cache = CliRuntimeStatusCache()
    return tuple(CliBackendModelInventoryAdapter(backend_id, cache) for backend_id in INVENTORIED_CLI_BACKENDS)


__all__ = [
    "CliBackendModelInventoryAdapter",
    "CliRuntimeStatusCache",
    "INVENTORIED_CLI_BACKENDS",
    "build_cli_model_inventory_adapters",
]
