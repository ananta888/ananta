"""Deterministic Replay Engine (SIM-023).

Re-runs a simulation from a checkpoint using the same random seed and
a scripted (recorded) adapter so results are reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from simulation.adapters.dummy import ScriptedAdapter
from simulation.adapters.model_strategy import ModelStrategyResolver
from simulation.engine.budget_guard import BudgetGuard
from simulation.engine.checkpoint import CheckpointManager
from simulation.engine.tick_runner import TickResult, TickRunner
from simulation.models.scenario import BudgetConfig, ScenarioConfig
from simulation.models.world_state import WorldState


class ReplayTrace:
    """Recorded decisions from a live run, used for replay."""

    def __init__(self) -> None:
        self._ticks: list[dict[str, Any]] = []

    def record(self, result: TickResult) -> None:
        self._ticks.append({
            "tick": result.tick,
            "decisions": {
                aid: d.get("proposal", {}).get("action_type", "noop")
                for aid, d in result.agent_decisions.items()
            },
        })

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self._ticks, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ReplayTrace":
        rt = cls()
        rt._ticks = json.loads(Path(path).read_text(encoding="utf-8"))
        return rt

    def actions_for_agent(self, agent_id: str) -> list[str]:
        return [t["decisions"].get(agent_id, "noop") for t in self._ticks]


class ReplayRunner:
    """Runs a simulation replay from a checkpoint + trace."""

    def __init__(self, checkpoint: WorldState, trace: ReplayTrace,
                  scenario: ScenarioConfig) -> None:
        self._initial_state = checkpoint.snapshot()
        self._trace = trace
        self._scenario = scenario

    def run(self) -> Iterator[TickResult]:
        state = self._initial_state.snapshot()

        # Build scripted adapters per agent
        from simulation.models.scenario import ModelStrategyEntry

        class _ReplayFactory:
            def build(self, entry: ModelStrategyEntry):  # type: ignore[override]
                return ScriptedAdapter(["noop"])  # placeholder; per-agent set below

        resolver = ModelStrategyResolver(self._scenario)

        # Override per agent
        agent_adapters = {}
        for agent_id in state.agents:
            actions = self._trace.actions_for_agent(agent_id)
            if not actions:
                actions = ["noop"]
            agent_adapters[agent_id] = ScriptedAdapter(actions)

        class _PerAgentResolver:
            def resolve(self, agent_id: str):
                return agent_adapters.get(agent_id, ScriptedAdapter(["noop"]))

        runner = TickRunner(strategy_resolver=_PerAgentResolver())  # type: ignore[arg-type]
        budget = BudgetGuard(self._scenario.budget)

        while True:
            result = runner.run_tick(state, budget)
            yield result
            if result.budget_violation:
                break
