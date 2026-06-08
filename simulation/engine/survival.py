"""Survival/Health/Energy/Death model — tick-level decay (SIM-008)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulation.models.world_state import AgentState, SimEvent, WorldState


@dataclass
class SurvivalConfig:
    hunger_per_tick: float = 0.05       # hunger rises this much per tick
    energy_regen_rest: float = 0.2      # energy gained from rest action
    energy_drain_active: float = 0.02   # energy drained per active tick
    health_drain_starving: float = 0.1  # health lost per tick when hunger >= 0.9
    health_drain_no_shelter: float = 0.02
    morale_drain_unhealthy: float = 0.03
    morale_drain_hungry: float = 0.02
    death_health_threshold: float = 0.0  # health <= 0 → dead


class SurvivalSystem:
    """Applied once per tick to all living agents."""

    def __init__(self, config: SurvivalConfig | None = None) -> None:
        self.config = config or SurvivalConfig()

    def tick(self, state: WorldState) -> list[SimEvent]:
        events: list[SimEvent] = []
        cfg = self.config

        for agent in list(state.agents.values()):
            if not agent.alive:
                continue

            # Hunger accumulates
            agent.hunger = min(1.0, agent.hunger + cfg.hunger_per_tick)

            # Location regen
            loc = state.locations.get(agent.location_id)
            if loc:
                for resource, regen in loc.resource_regen.items():
                    loc.resources[resource] = min(
                        loc.resources.get(resource, 0.0) + regen,
                        loc.resources.get(f"{resource}_max", 1e9),
                    )

            # Health damage from starvation
            if agent.hunger >= 0.9:
                agent.health = max(0.0, agent.health - cfg.health_drain_starving)

            # Health drain from no shelter
            if agent.shelter_status == "outdoors":
                agent.health = max(0.0, agent.health - cfg.health_drain_no_shelter)

            # Morale decay
            if agent.health < 0.4:
                agent.morale = max(0.0, agent.morale - cfg.morale_drain_unhealthy)
            if agent.hunger > 0.7:
                agent.morale = max(0.0, agent.morale - cfg.morale_drain_hungry)

            # Energy passive drain
            agent.energy = max(0.0, agent.energy - cfg.energy_drain_active)

            # Death check
            if agent.health <= cfg.death_health_threshold:
                agent.alive = False
                ev = SimEvent(tick=state.tick, kind="death", actor_id=agent.id,
                              description=f"{agent.name} died (health={agent.health:.2f})",
                              data={"cause": _death_cause(agent)})
                state.apply_event(ev)
                events.append(ev)

        return events


def _death_cause(agent: AgentState) -> str:
    if agent.hunger >= 0.9:
        return "starvation"
    if agent.shelter_status == "outdoors":
        return "exposure"
    return "health_depletion"
