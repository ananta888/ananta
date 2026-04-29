from __future__ import annotations

from worker.planning.step_graph import build_step_graph
from worker.planning.step_scheduler import StepBudgets, consume_step_budget, select_next_step


def test_step_scheduler_skips_policy_denied_steps() -> None:
    graph = build_step_graph(steps=[{"step_id": "safe"}, {"step_id": "danger"}])
    step = select_next_step(
        graph=graph,
        policy_decisions={"safe": "allow", "danger": "deny"},
        profile="balanced",
    )
    assert step is not None
    assert step["step_id"] == "safe"


def test_step_budget_exhaustion_is_explicit() -> None:
    status = consume_step_budget(
        counters={"tokens_used": 3900, "runtime_used": 10, "commands_used": 0, "patch_attempts": 0},
        budgets=StepBudgets(max_tokens=4000, max_runtime_seconds=30, max_commands=3, max_patch_attempts=2),
        token_cost=200,
        runtime_seconds=1,
        command_count=1,
    )
    assert status["budget_exhausted"] is True
    assert status["stop_reason"] == "budget_exhausted"

