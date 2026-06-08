"""Optional n8n Wrapper for Trigger and Reporting (SIM-040).

Provides a webhook receiver that n8n can POST to in order to trigger
simulation runs, and an n8n-compatible output payload for downstream
workflow nodes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class N8NTriggerPayload:
    """Shape of the JSON body n8n sends to start a simulation."""
    scenario_name: str = "survival_island"
    tick_limit: int = 10
    seed: int = 42
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "N8NTriggerPayload":
        return cls(
            scenario_name=d.get("scenario_name", "survival_island"),
            tick_limit=int(d.get("tick_limit", 10)),
            seed=int(d.get("seed", 42)),
            metadata=d.get("metadata"),
        )


def handle_n8n_trigger(payload: dict[str, Any]) -> dict[str, Any]:
    """Process an n8n webhook trigger and return n8n-compatible output.

    Can be registered as a Flask route or called inline from a webhook handler.
    Returns a JSON-serializable dict that n8n's HTTP-request node can consume.
    """
    try:
        trigger = N8NTriggerPayload.from_dict(payload)
        from simulation.engine.batch_runner import BatchRunner
        from simulation.scenarios.standard_scenarios import get_scenario
        from simulation.models.scenario import BudgetConfig

        scenario = get_scenario(trigger.scenario_name)
        patched = scenario.model_copy(
            update={"budget": BudgetConfig(max_ticks=trigger.tick_limit,
                                            stop_on_extinction=True),
                     "seed": trigger.seed}
        )
        runner = BatchRunner()
        results = runner.run([patched])
        r = results[0]
        return {
            "status": "ok",
            "run_id": r.run_id,
            "scenario": trigger.scenario_name,
            "ticks_run": r.ticks_run,
            "outcome": r.report.get("outcome", {}),
            "survival_rate_pct": r.report.get("metrics", {}).get("survival_rate_pct"),
            "report": r.report,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def register_n8n_routes(app: Any) -> None:
    """Register Flask routes for n8n integration."""
    try:
        from flask import jsonify, request  # type: ignore
    except ImportError:
        return

    @app.route("/api/simulation/n8n/trigger", methods=["POST"])
    def n8n_trigger():
        payload = request.get_json(force=True) or {}
        result = handle_n8n_trigger(payload)
        return jsonify(result), 200 if result.get("status") == "ok" else 500
