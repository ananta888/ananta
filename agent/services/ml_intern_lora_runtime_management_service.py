"""Hub-owned cache management and rollback commands for LoRA inference."""

from __future__ import annotations

import hashlib
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
from agent.services.unsloth_runtime_endpoint_registry_service import (
    RuntimeEndpointRegistryError,
    RuntimeEndpointRegistryPort,
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
        endpoint_registry: RuntimeEndpointRegistryPort | None = None,
    ) -> None:
        self._registry = registry
        self._inference = inference
        self._endpoint_registry = endpoint_registry

    def capabilities(self) -> Mapping[str, Any]:
        result = dict(self._inference.capabilities())
        result["runtime_handoff"] = {
            "available": self._endpoint_registry is not None,
            "reason_code": (
                None
                if self._endpoint_registry is not None
                else "runtime_endpoint_registry_unconfigured"
            ),
            "api_capabilities": [
                "anthropic_messages",
                "openai_chat",
                "openai_responses",
                "streaming",
                "structured_output",
                "tools",
            ],
            "implicit_fallback": False,
        }
        return result

    def rollback_endpoint(
        self,
        *,
        endpoint_id: str,
        reason: str,
        tenant_id: str,
        owner_subject: str,
        expected_revision: int | None,
    ) -> Mapping[str, Any]:
        normalized_reason = _reason(reason)
        if self._endpoint_registry is None:
            raise LoraRuntimeManagementError(
                "runtime_endpoint_registry_unconfigured",
                "Runtime endpoint registry is not configured.",
            )
        if expected_revision is None:
            raise LoraRuntimeManagementError(
                "runtime_endpoint_revision_required",
                "Endpoint rollback requires expected_version.",
            )
        try:
            revision = self._endpoint_registry.rollback(
                tenant_id=tenant_id,
                endpoint_id=endpoint_id,
                expected_revision=expected_revision,
                reason_sha256=hashlib.sha256(
                    normalized_reason.encode("utf-8")
                ).hexdigest(),
                actor_id=owner_subject,
            )
        except RuntimeEndpointRegistryError as exc:
            raise LoraRuntimeManagementError(
                exc.reason_code,
                str(exc),
                retryable=exc.retryable,
            ) from exc
        return {
            **revision.public_summary(),
            "policy_decision": {
                "policy_version": "mlintern-runtime-endpoint-v1",
                "decision": "restore_previous_immutable_endpoint_revision",
                "reason_code": "runtime_endpoint_rolled_back",
                "adapter_promotion_changed": False,
                "provenance_changed": False,
                "implicit_fallback": False,
            },
        }

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

    def quarantine_dataset_adapters(
        self,
        *,
        dataset_hashes: list[str],
        reason: str,
        tenant_id: str,
        owner_subject: str,
    ) -> list[dict[str, Any]]:
        """Deprecate or terminally fence every adapter derived from revoked datasets."""

        normalized_hashes = {str(value or "").strip().lower() for value in dataset_hashes}
        if not normalized_hashes or any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in normalized_hashes
        ):
            raise LoraRuntimeManagementError(
                "adapter_quarantine_dataset_hash_invalid",
                "Dataset quarantine requires lowercase SHA-256 digests.",
            )
        outcomes = []
        records = self._registry.list_adapters(tenant_id=tenant_id, owner_subject=owner_subject)
        for record in records:
            if record.dataset_hash not in normalized_hashes:
                continue
            if record.status == "approved":
                result = self.rollback(
                    adapter_id=record.adapter_id,
                    reason=reason,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    expected_version=record.registry_version,
                )
                outcomes.append(dict(result))
            elif record.status == "evaluated":
                rejected = self._registry.reject(
                    record.adapter_id,
                    reason=reason,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    expected_version=record.registry_version,
                )
                outcomes.append(
                    {
                        "adapter_id": rejected.adapter_id,
                        "version": rejected.version,
                        "registry_version": rejected.registry_version,
                        "status": rejected.status,
                        "rollback_target": {"type": "base_model_only", "base_model": rejected.base_model},
                        "cache_unload": {"status": "not_loaded", "reason_code": "adapter_not_approved"},
                    }
                )
            elif record.status in {"created", "training", "trained"}:
                failed = self._registry.transition(
                    record.adapter_id,
                    "failed",
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    expected_version=record.registry_version,
                )
                outcomes.append(
                    {
                        "adapter_id": failed.adapter_id,
                        "version": failed.version,
                        "registry_version": failed.registry_version,
                        "status": failed.status,
                        "rollback_target": {"type": "base_model_only", "base_model": failed.base_model},
                        "cache_unload": {"status": "not_loaded", "reason_code": "adapter_not_approved"},
                    }
                )
        return outcomes


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
