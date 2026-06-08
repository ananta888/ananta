"""CLI commands for the Emergence Simulation Lab (SIM-037)."""
from __future__ import annotations

import json
from typing import Any


def cmd_sim(args: list[str], output_fn: Any = print) -> dict[str, Any]:
    """Entry point for :sim commands in operator TUI.

    sub-commands:
      :sim list                  — list available scenarios
      :sim run <scenario>        — run a scenario (dummy adapter)
      :sim run <scenario> --ticks N
      :sim status                — last run summary
    """
    if not args:
        return _help(output_fn)

    sub = args[0]

    if sub == "list":
        return _list_scenarios(output_fn)
    elif sub == "run":
        scenario_name = args[1] if len(args) > 1 else "survival_island"
        ticks = 10
        for i, arg in enumerate(args):
            if arg == "--ticks" and i + 1 < len(args):
                try:
                    ticks = int(args[i + 1])
                except ValueError:
                    pass
        return _run_scenario(scenario_name, ticks, output_fn)
    elif sub in ("help", "--help", "-h"):
        return _help(output_fn)
    else:
        output_fn(f"[sim] unknown sub-command: {sub!r}")
        return _help(output_fn)


def _help(output_fn: Any) -> dict[str, Any]:
    output_fn(
        "sim: :sim list | :sim run <scenario> [--ticks N] | :sim status"
    )
    return {"ok": True, "sub": "help"}


def _list_scenarios(output_fn: Any) -> dict[str, Any]:
    try:
        from simulation.scenarios.standard_scenarios import list_scenarios
        names = list_scenarios()
        output_fn("Available scenarios: " + ", ".join(names))
        return {"ok": True, "scenarios": names}
    except Exception as exc:
        output_fn(f"[sim] error: {exc}")
        return {"ok": False, "error": str(exc)}


def _run_scenario(name: str, ticks: int, output_fn: Any) -> dict[str, Any]:
    try:
        from simulation.engine.batch_runner import BatchRunner, _build_world
        from simulation.scenarios.standard_scenarios import get_scenario
        from simulation.models.scenario import BudgetConfig

        scenario = get_scenario(name)
        # Override tick limit
        patched = scenario.model_copy(
            update={"budget": BudgetConfig(max_ticks=ticks, stop_on_extinction=True)}
        )
        runner = BatchRunner()
        results = runner.run([patched])
        r = results[0]
        output_fn(f"[sim] {name} done — {r.ticks_run} ticks in {r.elapsed_seconds:.1f}s")
        outcome = r.report.get("outcome", {})
        output_fn(f"[sim] outcome: {outcome.get('category')} ({outcome.get('severity')}): "
                  f"{outcome.get('description')}")
        return {"ok": True, "run_id": r.run_id, "report": r.report}
    except Exception as exc:
        output_fn(f"[sim] error: {exc}")
        return {"ok": False, "error": str(exc)}
