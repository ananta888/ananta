from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class ComponentRegistry(Generic[T]):
    def __init__(self, *, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, Callable[[], T]] = {}

    def register_factory(self, name: str, factory: Callable[[], T]) -> None:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError(f"{self._kind} name must not be empty")
        if normalized in self._factories:
            raise ValueError(f"{self._kind} '{normalized}' already registered")
        self._factories[normalized] = factory

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories.keys()))

    def create(self, name: str) -> T:
        normalized = str(name or "").strip()
        factory = self._factories.get(normalized)
        if factory is None:
            options = ", ".join(self.names()) or "<none>"
            raise KeyError(f"unknown {self._kind} '{normalized}', available: {options}")
        return factory()

    def has(self, name: str) -> bool:
        return str(name or "").strip() in self._factories


class ViewRegistry(ComponentRegistry[object]):
    def __init__(self) -> None:
        super().__init__(kind="view")


class RendererRegistry(ComponentRegistry[object]):
    def __init__(self) -> None:
        super().__init__(kind="renderer")


class OutputAdapterRegistry(ComponentRegistry[object]):
    def __init__(self) -> None:
        super().__init__(kind="output adapter")


@dataclass(frozen=True)
class RegistryBundle:
    views: ViewRegistry
    renderers: RendererRegistry
    adapters: OutputAdapterRegistry

