from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

_FORBIDDEN_METADATA_KEY_PARTS = frozenset(
    {"api_key", "authorization", "credential", "password", "secret", "token"}
)


def _assert_safe_metadata(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _FORBIDDEN_METADATA_KEY_PARTS):
                raise ValueError(f"provider_capability_sensitive_metadata_denied:{path}.{key}")
            _assert_safe_metadata(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe_metadata(item, path=f"{path}[{index}]")


@dataclass(frozen=True)
class ProviderCapability:
    provider_id: str
    provider_family: str
    source: str
    status: str
    capabilities: tuple[str, ...] = ()
    locality: str = "unknown"
    privacy_class: str = "unknown"
    cost_class: str = "unknown"
    latency_class: str = "unknown"
    credential_ref: str = ""
    models: tuple[str, ...] = ()
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.provider_id or "").strip():
            raise ValueError("provider_id_required")
        if not str(self.provider_family or "").strip():
            raise ValueError("provider_family_required")
        if self.credential_ref and ":" not in self.credential_ref:
            raise ValueError("credential_ref_must_be_opaque_reference")
        _assert_safe_metadata(self.metadata)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.provider_capability.v1",
            "provider_id": self.provider_id,
            "provider_family": self.provider_family,
            "source": self.source,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "locality": self.locality,
            "privacy_class": self.privacy_class,
            "cost_class": self.cost_class,
            "latency_class": self.latency_class,
            "credential_ref": self.credential_ref,
            "models": list(self.models),
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


class ProviderCapabilitySource(Protocol):
    source_id: str

    def list_capabilities(self) -> list[ProviderCapability]: ...


class ProviderCapabilityPort(Protocol):
    """Hub-side discovery/selection seam; never exposes provider runtimes."""

    def list_capabilities(self) -> list[ProviderCapability]: ...

    def select(
        self, requirement: "ProviderSelectionRequirement"
    ) -> "ProviderCapabilityDecision": ...


@dataclass(frozen=True)
class ProviderSelectionRequirement:
    required_capabilities: tuple[str, ...] = ()
    allowed_provider_ids: tuple[str, ...] = ()
    allowed_localities: tuple[str, ...] = ()
    maximum_privacy_class: str = ""


@dataclass(frozen=True)
class ProviderCapabilityDecision:
    status: str
    selected: ProviderCapability | None
    reason_code: str
    rejected: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.provider_capability_decision.v1",
            "status": self.status,
            "selected": self.selected.as_dict() if self.selected else None,
            "reason_code": self.reason_code,
            "rejected": [dict(item) for item in self.rejected],
        }


class ProviderCapabilityService:
    """Read-only consolidation over existing registries.

    Sources remain container-owned. The service only consumes their safe,
    versioned descriptors and never stores credentials or provider runtimes.
    """

    _PRIVACY_RANK = {
        "public": 0,
        "internal": 1,
        "restricted": 2,
        "confidential": 3,
        "secret": 4,
        "unknown": 99,
    }

    def __init__(self, sources: Sequence[ProviderCapabilitySource] = ()) -> None:
        self._sources = tuple(sources)

    def list_capabilities(self) -> list[ProviderCapability]:
        rows: list[ProviderCapability] = []
        for source in self._sources:
            rows.extend(source.list_capabilities())
        return sorted(rows, key=lambda item: (item.provider_family, item.provider_id, item.source))

    @classmethod
    def _privacy_allowed(cls, actual: str, maximum: str) -> bool:
        if not maximum:
            return True
        return cls._PRIVACY_RANK.get(actual, 99) <= cls._PRIVACY_RANK.get(maximum, -1)

    def select(self, requirement: ProviderSelectionRequirement) -> ProviderCapabilityDecision:
        required = {item.strip().lower() for item in requirement.required_capabilities if item.strip()}
        allowed_ids = {item.strip().lower() for item in requirement.allowed_provider_ids if item.strip()}
        allowed_localities = {item.strip().lower() for item in requirement.allowed_localities if item.strip()}
        rejected: list[dict[str, str]] = []
        candidates: list[ProviderCapability] = []
        for row in self.list_capabilities():
            reason = ""
            if row.status not in {"available", "ready", "healthy", "declared"}:
                reason = "provider_not_ready"
            elif allowed_ids and row.provider_id.lower() not in allowed_ids:
                reason = "provider_not_allowed"
            elif allowed_localities and row.locality.lower() not in allowed_localities:
                reason = "locality_not_allowed"
            elif not required.issubset({item.lower() for item in row.capabilities}):
                reason = "missing_capabilities"
            elif not self._privacy_allowed(row.privacy_class.lower(), requirement.maximum_privacy_class.lower()):
                reason = "privacy_class_not_allowed"
            if reason:
                rejected.append({"provider_id": row.provider_id, "source": row.source, "reason_code": reason})
            else:
                candidates.append(row)
        if not candidates:
            return ProviderCapabilityDecision(
                status="incompatible",
                selected=None,
                reason_code="no_compatible_provider",
                rejected=tuple(rejected),
            )
        locality_rank = {"local": 0, "private": 1, "cloud": 2, "unknown": 3}
        selected = sorted(
            candidates,
            key=lambda item: (
                locality_rank.get(item.locality, 3),
                item.cost_class,
                item.latency_class,
                item.provider_id,
                item.source,
            ),
        )[0]
        return ProviderCapabilityDecision(
            status="selected",
            selected=selected,
            reason_code="capabilities_satisfied",
            rejected=tuple(rejected),
        )


class GenericProviderRegistryCapabilitySource:
    source_id = "agent.providers.registry"

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def list_capabilities(self) -> list[ProviderCapability]:
        rows: list[ProviderCapability] = []
        for descriptor in self._registry.list_descriptors():
            rows.append(
                ProviderCapability(
                    provider_id=descriptor.provider_id,
                    provider_family=descriptor.provider_family,
                    source=self.source_id,
                    status="declared" if descriptor.enabled_by_default else "disabled",
                    capabilities=tuple(descriptor.capabilities),
                    locality="unknown",
                    privacy_class="unknown",
                    reason="" if descriptor.enabled_by_default else "provider_disabled_by_default",
                    metadata={"risk_class": descriptor.risk_class},
                )
            )
        return rows


class WorkerProviderRegistryCapabilitySource:
    source_id = "worker.core.provider_registry"

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def list_capabilities(self) -> list[ProviderCapability]:
        diagnostics = {
            str(item.get("provider_id") or "").lower(): item
            for item in self._registry.diagnostics()
        }
        rows: list[ProviderCapability] = []
        provider_info = getattr(self._registry, "capability_info", self._registry.provider_info)
        for item in provider_info():
            provider_id = str(item.get("id") or "")
            diag = diagnostics.get(provider_id.lower(), {})
            status = str(diag.get("status") or "declared")
            locality = "cloud" if str(item.get("kind") or "") == "cloud" else "local"
            capabilities = ["text_generation"]
            if item.get("supports_tools"):
                capabilities.append("tool_calling")
            if item.get("supports_streaming"):
                capabilities.append("streaming")
            latency_ms = diag.get("latency_ms")
            latency_class = "unknown"
            if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool):
                latency_class = "fast" if latency_ms <= 200 else "standard" if latency_ms <= 2000 else "slow"
            rows.append(
                ProviderCapability(
                    provider_id=provider_id,
                    provider_family="llm",
                    source=self.source_id,
                    status=status,
                    capabilities=tuple(capabilities),
                    locality=locality,
                    privacy_class="confidential" if locality == "local" else "public",
                    cost_class="local" if locality == "local" else "metered",
                    latency_class=latency_class,
                    credential_ref=str(item.get("credential_ref") or ""),
                    models=tuple([str(item.get("default_model"))] if item.get("default_model") else []),
                    reason=str(diag.get("error_detail") or ""),
                )
            )
        return rows


class ModelInferenceRegistryCapabilitySource:
    """Adapter for the existing restricted-inference engine registry."""

    source_id = "agent.services.model_inference_adapter_registry"

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def list_capabilities(self) -> list[ProviderCapability]:
        rows: list[ProviderCapability] = []
        for engine, capabilities in self._registry.capabilities().items():
            rows.append(
                ProviderCapability(
                    provider_id=str(engine),
                    provider_family="restricted_inference",
                    source=self.source_id,
                    status="declared",
                    capabilities=tuple(str(item) for item in capabilities),
                    locality="local",
                    privacy_class="confidential",
                    cost_class="local",
                    latency_class="unknown",
                )
            )
        return rows


__all__ = [
    "GenericProviderRegistryCapabilitySource",
    "ModelInferenceRegistryCapabilitySource",
    "ProviderCapability",
    "ProviderCapabilityDecision",
    "ProviderCapabilityPort",
    "ProviderCapabilityService",
    "ProviderCapabilitySource",
    "ProviderSelectionRequirement",
    "WorkerProviderRegistryCapabilitySource",
]
