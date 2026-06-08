"""Core Metrics Engine (SIM-025)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulation.models.world_state import WorldState


@dataclass
class TickSnapshot:
    tick: int
    living_count: int
    avg_health: float
    avg_hunger: float
    avg_energy: float
    avg_morale: float
    avg_reputation: float
    total_crimes: int
    total_deaths: int
    state_hash: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class MetricsCollector:
    """Collects per-tick snapshots; produces aggregate summary."""

    def __init__(self) -> None:
        self._snapshots: list[TickSnapshot] = []
        self._crimes_by_tick: dict[int, int] = {}
        self._deaths_by_tick: dict[int, int] = {}

    def record_tick(self, state: WorldState) -> TickSnapshot:
        living = state.living_agents()
        n = len(living) or 1

        crimes = sum(1 for e in state.events if e.kind == "crime" and e.tick == state.tick - 1)
        deaths = sum(1 for e in state.events if e.kind == "death" and e.tick == state.tick - 1)

        snap = TickSnapshot(
            tick=state.tick,
            living_count=len(living),
            avg_health=sum(a.health for a in living) / n,
            avg_hunger=sum(a.hunger for a in living) / n,
            avg_energy=sum(a.energy for a in living) / n,
            avg_morale=sum(a.morale for a in living) / n,
            avg_reputation=sum(a.reputation for a in living) / n,
            total_crimes=crimes,
            total_deaths=deaths,
            state_hash=state.state_hash(),
        )
        self._snapshots.append(snap)
        return snap

    def summary(self) -> dict[str, Any]:
        if not self._snapshots:
            return {}
        last = self._snapshots[-1]
        first = self._snapshots[0]
        total_crimes = sum(s.total_crimes for s in self._snapshots)
        total_deaths = sum(s.total_deaths for s in self._snapshots)
        survived_pct = (last.living_count / max(1, first.living_count)) * 100
        return {
            "ticks_run": len(self._snapshots),
            "final_living": last.living_count,
            "initial_living": first.living_count,
            "survival_rate_pct": round(survived_pct, 1),
            "total_crimes": total_crimes,
            "total_deaths": total_deaths,
            "avg_health_final": round(last.avg_health, 3),
            "avg_morale_final": round(last.avg_morale, 3),
            "avg_hunger_final": round(last.avg_hunger, 3),
            "timeline": [s.as_dict() for s in self._snapshots],
        }

    def snapshots(self) -> list[TickSnapshot]:
        return list(self._snapshots)
