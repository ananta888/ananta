from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from agent.services.evolution.models import (
    ApplyResult,
    EvolutionCapability,
    EvolutionContext,
    EvolutionProposal,
    EvolutionProviderDescriptor,
    EvolutionResult,
    ValidationResult,
)


class UnsupportedEvolutionOperation(RuntimeError):
    def __init__(self, provider_name: str, operation: str):
        self.provider_name = provider_name
        self.operation = operation
        super().__init__(f"evolution_operation_not_supported:{provider_name}:{operation}")


class EvolutionEngine(ABC):
    """Provider-facing SPI for analysis-first evolution integrations."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    def version(self) -> str:
        return "unknown"

    @property
    @abstractmethod
    def capabilities(self) -> Iterable[EvolutionCapability | str]:
        raise NotImplementedError

    def normalized_capabilities(self) -> list[EvolutionCapability]:
        normalized: list[EvolutionCapability] = []
        seen: set[EvolutionCapability] = set()
        for item in self.capabilities or []:
            capability = item if isinstance(item, EvolutionCapability) else EvolutionCapability(str(item))
            if capability not in seen:
                normalized.append(capability)
                seen.add(capability)
        return normalized

    def supports(self, capability: EvolutionCapability | str) -> bool:
        normalized = capability if isinstance(capability, EvolutionCapability) else EvolutionCapability(str(capability))
        return normalized in set(self.normalized_capabilities())

    def describe(self) -> EvolutionProviderDescriptor:
        return EvolutionProviderDescriptor(
            provider_name=self.provider_name,
            version=self.version,
            status="available",
            capabilities=self.normalized_capabilities(),
        )

    @abstractmethod
    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        raise NotImplementedError

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        raise UnsupportedEvolutionOperation(self.provider_name, EvolutionCapability.VALIDATE.value)

    def apply(self, context: EvolutionContext, proposal: EvolutionProposal) -> ApplyResult:
        raise UnsupportedEvolutionOperation(self.provider_name, EvolutionCapability.APPLY.value)
