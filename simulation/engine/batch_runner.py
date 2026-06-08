"""Batch Experiment Runner (SIM-031)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from simulation.adapters.model_strategy import ModelStrategyResolver
from simulation.engine.budget_guard import BudgetGuard
from simulation.engine.tick_runner import TickResult, TickRunner
from simulation.metrics.core_metrics import MetricsCollector
from simulation.metrics.report_generator import ReportGenerator
from simulation.models.scenario import ScenarioConfig
from simulation.models.world_state import AgentState, LocationState, LawState, WorldState


@dataclass
class BatchRunResult:
    run_id: str
    scenario_name: str
    report: dict[str, Any]
    ticks_run: int
    elapsed_seconds: float
    error: str | None = None


def _build_world(scenario: ScenarioConfig) -> WorldState:
    """Construct initial WorldState from ScenarioConfig."""
    from simulation.models.world_state import LawState
    state = WorldState(scenario_name=scenario.name)

    for loc_def in scenario.locations:
        loc = LocationState(id=loc_def["id"], name=loc_def.get("name", loc_def["id"]))
        state.locations[loc.id] = loc

    for res_def in scenario.resources:
        lid = res_def.location_id or list(state.locations.keys())[0]
        if lid in state.locations:
            state.locations[lid].resources[res_def.name] = res_def.initial_amount
            if res_def.regeneration_per_tick > 0:
                state.locations[lid].resource_regen[res_def.name] = res_def.regeneration_per_tick

    for ag_def in scenario.agents:
        ag_id = ag_def["id"]
        loc_id = ag_def.get("location_id", list(state.locations.keys())[0])
        ag = AgentState(id=ag_id, name=ag_def.get("name", ag_id),
                        role=ag_def.get("role", "citizen"), location_id=loc_id)
        ag.inventory = dict(ag_def.get("starting_inventory", {}))
        ag.profile_id = ag_def.get("profile_id", "")
        state.agents[ag_id] = ag
        if loc_id in state.locations:
            state.locations[loc_id].occupants.append(ag_id)

    for law_def in scenario.laws:
        state.laws[law_def.id] = LawState(
            id=law_def.id, description=law_def.description,
            forbidden_actions=list(law_def.forbidden_actions),
            penalty=law_def.penalty, severity=law_def.severity,
        )

    return state


class BatchRunner:
    """Runs multiple scenarios or multiple seeds of the same scenario."""

    def run(
        self,
        scenarios: list[ScenarioConfig],
        on_tick: Callable[[str, TickResult], None] | None = None,
    ) -> list[BatchRunResult]:
        results: list[BatchRunResult] = []
        for scenario in scenarios:
            result = self._run_one(scenario, on_tick)
            results.append(result)
        return results

    def _run_one(self, scenario: ScenarioConfig,
                  on_tick: Callable[[str, TickResult], None] | None) -> BatchRunResult:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        state = _build_world(scenario)
        resolver = ModelStrategyResolver(scenario)
        runner = TickRunner(strategy_resolver=resolver)
        budget = BudgetGuard(scenario.budget)
        metrics = MetricsCollector()
        t0 = time.monotonic()
        violation = None
        ticks_run = 0

        try:
            while True:
                metrics.record_tick(state)
                result = runner.run_tick(state, budget)
                ticks_run += 1
                if on_tick:
                    on_tick(run_id, result)
                if result.budget_violation:
                    violation = result.budget_violation
                    break
        except Exception as exc:
            report = {"error": str(exc)}
            return BatchRunResult(run_id=run_id, scenario_name=scenario.name,
                                   report=report, ticks_run=ticks_run,
                                   elapsed_seconds=time.monotonic() - t0,
                                   error=str(exc))

        gen = ReportGenerator(run_id, scenario.name)
        report = gen.generate(metrics, state, budget, violation)
        return BatchRunResult(run_id=run_id, scenario_name=scenario.name,
                               report=report, ticks_run=ticks_run,
                               elapsed_seconds=time.monotonic() - t0)
