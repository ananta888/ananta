from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import Flask

from agent.services.evolution.engine import EvolutionEngine
from agent.services.evolution.models import EvolutionCapability, EvolutionProviderDescriptor


class EvolutionRegistryError(RuntimeError):
    pass


class EvolutionProviderNotFound(EvolutionRegistryError):
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        super().__init__(f"evolution_provider_not_found:{provider_name}")


class NoEvolutionProviderAvailable(EvolutionRegistryError):
    def __init__(self):
        super().__init__("no_evolution_provider_available")


@dataclass
class EvolutionProviderRegistry:
    """Runtime registry for provider-neutral evolution engines."""

    _providers: dict[str, EvolutionEngine] = field(default_factory=dict)
    _default_provider_name: str | None = None

    @property
    def default_provider_name(self) -> str | None:
        return self._default_provider_name

    def register(self, engine: EvolutionEngine, *, default: bool = False, replace: bool = False) -> EvolutionEngine:
        name = self._normalize_name(engine.provider_name)
        if not name:
            raise ValueError("evolution_provider_name_required")
        if not engine.supports(EvolutionCapability.ANALYZE):
            raise ValueError("evolution_provider_must_support_analyze")
        if name in self._providers and not replace:
            raise ValueError(f"evolution_provider_already_registered:{name}")

        self._providers[name] = engine
        if default or self._default_provider_name is None:
            self._default_provider_name = name
        return engine

    def unregister(self, provider_name: str) -> None:
        name = self._normalize_name(provider_name)
        self._providers.pop(name, None)
        if self._default_provider_name == name:
            self._default_provider_name = next(iter(sorted(self._providers.keys())), None)

    def get(self, provider_name: str) -> EvolutionEngine:
        name = self._normalize_name(provider_name)
        provider = self._providers.get(name)
        if provider is None:
            raise EvolutionProviderNotFound(name)
        return provider

    def resolve(self, provider_name: str | None = None, *, config: dict[str, Any] | None = None) -> EvolutionEngine:
        configured = ""
        if isinstance(config, dict):
            evolution_cfg = config.get("evolution") if isinstance(config.get("evolution"), dict) else {}
            configured_value = (
                evolution_cfg.get("default_provider")
                if "evolution" in config
                else config.get("default_provider") or ""
            )
            configured = str(configured_value or "").strip()
        selected = self._normalize_name(provider_name or configured or self._default_provider_name or "")
        if selected:
            return self.get(selected)
        raise NoEvolutionProviderAvailable()

    def list_descriptors(self) -> list[dict[str, Any]]:
        default_name = self._default_provider_name
        items: list[dict[str, Any]] = []
        for name in sorted(self._providers.keys()):
            try:
                descriptor = self._providers[name].describe().model_dump(mode="json")
            except Exception as exc:
                descriptor = {
                    "provider_name": name,
                    "version": "unknown",
                    "status": "unavailable",
                    "capabilities": [],
                    "provider_metadata": {"error": str(exc), "error_type": type(exc).__name__},
                }
            descriptor["default"] = name == default_name
            items.append(descriptor)
        return items

    def health(self, provider_name: str | None = None) -> dict[str, Any]:
        if provider_name:
            names = [self._normalize_name(provider_name)]
        else:
            names = sorted(self._providers.keys())
        providers = []
        for name in names:
            provider = self.get(name)
            try:
                descriptor = provider.describe().model_dump(mode="json")
                status = str(descriptor.get("status") or "available")
                error = None
            except Exception as exc:
                descriptor = {
                    "provider_name": name,
                    "version": "unknown",
                    "capabilities": [],
                    "provider_metadata": {},
                }
                status = "unavailable"
                error = {"message": str(exc), "type": type(exc).__name__}
            providers.append(
                {
                    **descriptor,
                    "status": status,
                    "default": name == self._default_provider_name,
                    "error": error,
                }
            )
        overall_status = (
            "available"
            if providers and all(item["status"] != "unavailable" for item in providers)
            else "degraded"
        )
        return {
            "status": overall_status,
            "default_provider": self._default_provider_name,
            "providers": providers,
        }

    def describe(self, provider_name: str) -> EvolutionProviderDescriptor:
        return self.get(provider_name).describe()

    def clear(self) -> None:
        self._providers.clear()
        self._default_provider_name = None

    @staticmethod
    def _normalize_name(provider_name: str | None) -> str:
        return str(provider_name or "").strip().lower()


evolution_provider_registry = EvolutionProviderRegistry()


def get_evolution_provider_registry() -> EvolutionProviderRegistry:
    return evolution_provider_registry


def register_evolution_provider(
    engine: EvolutionEngine,
    *,
    app: Flask | None = None,
    default: bool = False,
    replace: bool = False,
) -> EvolutionEngine:
    registry = get_evolution_provider_registry()
    registered = registry.register(engine, default=default, replace=replace)
    if app is not None:
        app.extensions["evolution_provider_registry"] = registry
        names = app.extensions.setdefault("evolution_providers", set())
        names.add(registered.provider_name)
    return registered
