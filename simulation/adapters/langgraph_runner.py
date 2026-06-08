"""Optional LangGraph Runner Adapter (SIM-039).

Design-only stub. Wraps TickRunner as a LangGraph-compatible node graph
when langgraph is installed. Import-safe: no langgraph at module load time.
"""
from __future__ import annotations

from typing import Any


class LangGraphSimAdapter:
    """Wraps a TickRunner as a LangGraph node.

    Usage (when langgraph is installed):
      graph = LangGraphSimAdapter(scenario).build_graph()
      result = graph.invoke({"tick_limit": 10})
    """

    def __init__(self, scenario: Any, runner: Any = None) -> None:
        self._scenario = scenario
        self._runner = runner

    def build_graph(self) -> Any:
        try:
            from langgraph.graph import StateGraph
        except ImportError:
            raise ImportError(
                "langgraph not installed. "
                "Install it with: pip install langgraph"
            )

        from simulation.engine.batch_runner import _build_world
        from simulation.engine.budget_guard import BudgetGuard
        from simulation.adapters.model_strategy import ModelStrategyResolver
        from simulation.engine.tick_runner import TickRunner

        scenario = self._scenario

        def sim_node(state: dict[str, Any]) -> dict[str, Any]:
            world_state = state.get("world_state") or _build_world(scenario)
            resolver = ModelStrategyResolver(scenario)
            runner = self._runner or TickRunner(strategy_resolver=resolver)
            budget = BudgetGuard(scenario.budget)
            results = []

            limit = state.get("tick_limit", scenario.budget.max_ticks)
            for _ in range(limit):
                result = runner.run_tick(world_state, budget)
                results.append(result.state_hash)
                if result.budget_violation:
                    break

            return {**state, "world_state": world_state,
                     "tick_hashes": results, "done": True}

        graph = StateGraph(dict)
        graph.add_node("simulate", sim_node)
        graph.set_entry_point("simulate")
        graph.set_finish_point("simulate")
        return graph.compile()
