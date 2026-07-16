"""Hub-owned cache management and rollback commands for LoRA inference."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryError,
    RegistryNotFoundError,
    RegistryVersionConflict,
)
from agent.services.ml_intern_lora_inference_service import (
    LoraInferenceError,
    MlInternLoraInferenceService,
)


class LoraRuntimeManagementError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


class MlInternLoraRuntimeManagementService:
    def __init__(
        self,
        *,
        registry: MlInternAdapterRegistryService,
        inference: MlInternLoraInferenceService,
    ) -> None:
        self._registry = registry
        self._inference = inference

    def capabilities(self) -> Mapping[str, Any]:
        return self._inference.capabilities()

    def unload(
        self,
        *,
        adapter_id: str,
        reason: str,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> Mapping[str, Any]:
        try:
            return self._inference.unload(
                adapter_id=adapter_id,
                reason=_reason(reason),
                tenant_id=tenant_id,
                owner_subject=owner_subject,
            )
        except LoraInferenceError as exc:
            raise LoraRuntimeManagementError(exc.reason_code, str(exc), retryable=exc.retryable) from exc

    def rollback(
        self,
        *,
        adapter_id: str,
        reason: str,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        normalized_reason = _reason(reason)
        try:
            deprecated, target = self._registry.rollback(
                adapter_id,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                expected_version=expected_version,
            )
        except RegistryVersionConflict as exc:
            raise LoraRuntimeManagementError(exc.reason_code, str(exc)) from exc
        except RegistryNotFoundError as exc:
            raise LoraRuntimeManagementError("adapter_not_found", str(exc)) from exc
        except RegistryError as exc:
            raise LoraRuntimeManagementError("adapter_rollback_rejected", str(exc)) from exc

        unload: Mapping[str, Any]
        try:
            unload = self._inference.unload(
                adapter_id=deprecated.adapter_id,
                reason=normalized_reason,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
            )
        except LoraInferenceError as exc:
            # Deprecation remains authoritative even if a worker is offline.
            unload = {
                "status": "degraded",
                "reason_code": exc.reason_code,
                "retryable": exc.retryable,
                "adapter_id": deprecated.adapter_id,
                "adapter_version": deprecated.version,
            }
        rollback_target = (
            {
                "type": "adapter",
                "adapter_id": target.adapter_id,
                "version": target.version,
                "status": target.status,
            }
            if target is not None
            else {
                "type": "base_model_only",
                "base_model": deprecated.base_model,
            }
        )
        return {
            "adapter_id": deprecated.adapter_id,
            "version": deprecated.version,
            "registry_version": deprecated.registry_version,
            "status": deprecated.status,
            "rollback_target": rollback_target,
            "cache_unload": dict(unload),
            "policy_decision": {
                "policy_version": "mlintern-lora-runtime-v2",
                "decision": "adapter_rollback",
                "reason_code": "approved_adapter_deprecated",
                "unapproved_fallback_allowed": False,
            },
        }


def _reason(value: str) -> str:
    normalized = str(value or "").strip()
    if not 10 <= len(normalized) <= 512:
        raise LoraRuntimeManagementError(
            "management_reason_invalid",
            "A meaningful bounded management reason is required",
        )
    return normalized


__all__ = [
    "LoraRuntimeManagementError",
    "MlInternLoraRuntimeManagementService",
]
