"""ModelStrategy Resolver (SIM-019).

Picks the right SimulationModelAdapter for a given agent based on
the ScenarioConfig.model_strategy list.
"""
from __future__ import annotations

from typing import Any

from simulation.adapters.base import SimulationModelAdapter
from simulation.adapters.dummy import DummyModelAdapter
from simulation.models.scenario import ModelStrategyEntry, ScenarioConfig


class ModelStrategyResolver:
    """Resolves which adapter to use per agent_id.

    Resolution order:
      1. Exact agent_id match in model_strategy
      2. Wildcard entry (agent_id is None)
      3. Fallback: DummyModelAdapter
    """

    def __init__(self, scenario: ScenarioConfig,
                  adapter_factory: "AdapterFactory | None" = None) -> None:
        self._strategy = scenario.model_strategy
        self._factory = adapter_factory or _DefaultAdapterFactory()
        self._cache: dict[str, SimulationModelAdapter] = {}

    def resolve(self, agent_id: str) -> SimulationModelAdapter:
        if agent_id in self._cache:
            return self._cache[agent_id]

        entry = self._find_entry(agent_id)
        adapter = self._factory.build(entry)
        self._cache[agent_id] = adapter
        return adapter

    def _find_entry(self, agent_id: str) -> ModelStrategyEntry:
        for e in self._strategy:
            if e.agent_id == agent_id:
                return e
        for e in self._strategy:
            if e.agent_id is None:
                return e
        return ModelStrategyEntry()  # defaults to dummy


class AdapterFactory:
    """Base factory — subclass to add real providers."""

    def build(self, entry: ModelStrategyEntry) -> SimulationModelAdapter:
        raise NotImplementedError


class _DefaultAdapterFactory(AdapterFactory):
    """Builds adapters for known providers; falls back to Dummy."""

    def build(self, entry: ModelStrategyEntry) -> SimulationModelAdapter:
        provider = entry.provider.lower()

        if provider == "dummy":
            return DummyModelAdapter()

        if provider == "ollama":
            try:
                from simulation.adapters.ollama import OllamaAdapter
                return OllamaAdapter(model=entry.model)
            except ImportError:
                return DummyModelAdapter()

        if provider == "openrouter":
            try:
                from simulation.adapters.openrouter import OpenRouterAdapter
                return OpenRouterAdapter(model=entry.model)
            except ImportError:
                return DummyModelAdapter()

        # Unknown provider — safe fallback
        return DummyModelAdapter()
