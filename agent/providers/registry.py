from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .interfaces import ProviderDescriptor, ProviderRuntime

ProviderFactory = Callable[[], ProviderRuntime]


@dataclass(frozen=True)
class ProviderResolution:
    provider_family: str
    provider_id: str
    status: str
    descriptor: ProviderDescriptor | None = None
    provider: ProviderRuntime | None = None
    reason: str | None = None


class GenericProviderRegistry:
    """Provider-neutral descriptor/factory registry with safe degraded resolution."""

    def __init__(self) -> None:
        self._descriptors: dict[tuple[str, str], ProviderDescriptor] = {}
        self._factories: dict[tuple[str, str], ProviderFactory] = {}
        self._enabled_overrides: dict[tuple[str, str], bool] = {}

    @staticmethod
    def _key(provider_family: str, provider_id: str) -> tuple[str, str]:
        family = str(provider_family or "").strip().lower()
        provider = str(provider_id or "").strip().lower()
        if not family:
            raise ValueError("provider_family_required")
        if not provider:
            raise ValueError("provider_id_required")
        return family, provider

    def register_descriptor(self, descriptor: ProviderDescriptor) -> None:
        key = self._key(descriptor.provider_family, descriptor.provider_id)
        self._descriptors[key] = descriptor

    def register_factory(self, *, provider_family: str, provider_id: str, factory: ProviderFactory) -> None:
        self._factories[self._key(provider_family, provider_id)] = factory

    def register_provider(self, *, descriptor: ProviderDescriptor, factory: ProviderFactory | None = None) -> None:
        self.register_descriptor(descriptor)
        if factory is not None:
            self.register_factory(
                provider_family=descriptor.provider_family,
                provider_id=descriptor.provider_id,
                factory=factory,
            )

    def set_provider_enabled(self, *, provider_family: str, provider_id: str, enabled: bool) -> None:
        self._enabled_overrides[self._key(provider_family, provider_id)] = bool(enabled)

    def list_descriptors(self, *, provider_family: str | None = None) -> list[ProviderDescriptor]:
        family_filter = str(provider_family or "").strip().lower() or None
        descriptors = list(self._descriptors.values())
        if family_filter is not None:
            descriptors = [item for item in descriptors if item.provider_family.lower() == family_filter]
        return sorted(descriptors, key=lambda item: (item.provider_family, item.provider_id))

    def resolve_provider(self, *, provider_family: str, provider_id: str, enable: bool = False) -> ProviderResolution:
        family, provider = self._key(provider_family, provider_id)
        key = (family, provider)
        descriptor = self._descriptors.get(key)
        if descriptor is None:
            return ProviderResolution(
                provider_family=family,
                provider_id=provider,
                status="unknown",
                reason="provider_not_registered",
            )

        enabled = bool(self._enabled_overrides.get(key, descriptor.enabled_by_default)) or bool(enable)
        if not enabled:
            return ProviderResolution(
                provider_family=family,
                provider_id=provider,
                status="disabled",
                descriptor=descriptor,
                reason="provider_disabled_by_default",
            )

        factory = self._factories.get(key)
        if factory is None:
            return ProviderResolution(
                provider_family=family,
                provider_id=provider,
                status="degraded",
                descriptor=descriptor,
                reason="provider_factory_not_registered",
            )

        try:
            runtime = factory()
        except (ModuleNotFoundError, ImportError) as exc:
            return ProviderResolution(
                provider_family=family,
                provider_id=provider,
                status="degraded",
                descriptor=descriptor,
                reason=f"missing_optional_dependency:{exc}",
            )

        return ProviderResolution(
            provider_family=family,
            provider_id=provider,
            status="available",
            descriptor=descriptor,
            provider=runtime,
        )
