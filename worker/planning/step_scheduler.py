from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.core.execution_profile import normalize_execution_profile
from worker.planning.step_graph import ready_steps


@dataclass(frozen=True)
class StepBudgets:
    max_tokens: int = 4000
    max_runtime_seconds: int = 180
    max_commands: int = 4
    max_patch_attempts: int = 2


def select_next_step(
    *,
    graph: dict[str, dict[str, Any]],
    policy_decisions: dict[str, str],
    profile: str,
) -> dict[str, Any] | None:
    _ = normalize_execution_profile(profile)
    for step_id in ready_steps(graph=graph):
        decision = str(policy_decisions.get(step_id) or "allow").strip().lower()
        if decision == "deny":
            graph[step_id]["state"] = "blocked"
            continue
        graph[step_id]["state"] = "ready"
        return graph[step_id]
    return None


def consume_step_budget(*, counters: dict[str, int], budgets: StepBudgets, token_cost: int, runtime_seconds: int, command_count: int) -> dict[str, Any]:
    updated = {
        "tokens_used": int(counters.get("tokens_used") or 0) + int(token_cost),
        "runtime_used": int(counters.get("runtime_used") or 0) + int(runtime_seconds),
        "commands_used": int(counters.get("commands_used") or 0) + int(command_count),
        "patch_attempts": int(counters.get("patch_attempts") or 0) + 1,
    }
    exhausted = (
        updated["tokens_used"] > int(budgets.max_tokens)
        or updated["runtime_used"] > int(budgets.max_runtime_seconds)
        or updated["commands_used"] > int(budgets.max_commands)
        or updated["patch_attempts"] > int(budgets.max_patch_attempts)
    )
    return {
        "counters": updated,
        "budget_exhausted": exhausted,
        "stop_reason": "budget_exhausted" if exhausted else "",
    }

