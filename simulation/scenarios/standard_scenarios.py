"""Standard Scenarios (SIM-030).

Pre-built ScenarioConfig objects for quick experiments.
"""
from __future__ import annotations

from simulation.models.scenario import (
    BudgetConfig, LawDefinition, ModelStrategyEntry,
    ResourceDefinition, ScenarioConfig,
)


def survival_island() -> ScenarioConfig:
    """3 agents, 1 island location, scarce food — tests starvation dynamics."""
    return ScenarioConfig(
        name="survival_island",
        description="Three castaways on a resource-scarce island",
        seed=42,
        tick_limit=30,
        agents=[
            {"id": "a1", "name": "Alice", "role": "hunter",
             "location_id": "island", "starting_inventory": {"wood": 2.0}},
            {"id": "a2", "name": "Bob", "role": "farmer",
             "location_id": "island", "starting_inventory": {}},
            {"id": "a3", "name": "Carol", "role": "medic",
             "location_id": "island", "starting_inventory": {"herbs": 3.0}},
        ],
        locations=[{"id": "island", "name": "Island"}],
        resources=[
            ResourceDefinition(name="food", initial_amount=5.0,
                                regeneration_per_tick=0.5, location_id="island"),
        ],
        laws=[],
        model_strategy=[ModelStrategyEntry(provider="dummy", model="dummy-v1")],
        budget=BudgetConfig(max_ticks=30, stop_on_extinction=True),
    )


def governance_experiment() -> ScenarioConfig:
    """5 agents with active governance and crime laws."""
    return ScenarioConfig(
        name="governance_experiment",
        description="Small society with voting and law enforcement",
        seed=100,
        tick_limit=50,
        agents=[
            {"id": f"agent{i}", "name": f"Citizen{i}", "role": "citizen",
             "location_id": "town"}
            for i in range(1, 6)
        ],
        locations=[{"id": "town", "name": "Town Square"}],
        resources=[
            ResourceDefinition(name="food", initial_amount=20.0,
                                regeneration_per_tick=2.0, location_id="town"),
            ResourceDefinition(name="gold", initial_amount=50.0,
                                regeneration_per_tick=0.0, location_id="town"),
        ],
        laws=[
            LawDefinition(id="no_attack", description="No violence",
                           forbidden_actions=["attack"], penalty="imprisonment", severity=0.8),
        ],
        model_strategy=[ModelStrategyEntry(provider="dummy", model="dummy-v1")],
        budget=BudgetConfig(max_ticks=50),
    )


def trade_network() -> ScenarioConfig:
    """5 agents in 3 locations; tests resource flow and trade."""
    return ScenarioConfig(
        name="trade_network",
        description="Multi-location trade experiment",
        seed=7,
        tick_limit=40,
        agents=[
            {"id": "merchant1", "name": "Merchant1", "role": "merchant",
             "location_id": "market", "starting_inventory": {"gold": 10.0}},
            {"id": "farmer1", "name": "Farmer1", "role": "farmer",
             "location_id": "farm", "starting_inventory": {"food": 15.0}},
            {"id": "farmer2", "name": "Farmer2", "role": "farmer",
             "location_id": "farm", "starting_inventory": {"food": 10.0}},
            {"id": "builder1", "name": "Builder1", "role": "builder",
             "location_id": "workshop", "starting_inventory": {"wood": 20.0}},
            {"id": "wanderer", "name": "Wanderer", "role": "explorer",
             "location_id": "market", "starting_inventory": {}},
        ],
        locations=[
            {"id": "market", "name": "Market"},
            {"id": "farm", "name": "Farm"},
            {"id": "workshop", "name": "Workshop"},
        ],
        resources=[
            ResourceDefinition(name="food", initial_amount=5.0,
                                regeneration_per_tick=1.0, location_id="farm"),
        ],
        model_strategy=[ModelStrategyEntry(provider="dummy", model="dummy-v1")],
        budget=BudgetConfig(max_ticks=40),
    )


_REGISTRY: dict[str, ScenarioConfig] = {}


def get_scenario(name: str) -> ScenarioConfig:
    if not _REGISTRY:
        for fn in (survival_island, governance_experiment, trade_network):
            sc = fn()
            _REGISTRY[sc.name] = sc
    if name not in _REGISTRY:
        raise KeyError(f"unknown scenario: {name!r}")
    return _REGISTRY[name]


def list_scenarios() -> list[str]:
    if not _REGISTRY:
        get_scenario("survival_island")  # trigger load
    return list(_REGISTRY.keys())
