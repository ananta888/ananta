"""BudgetGuard — enforces per-run resource limits (SIM-020)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from simulation.models.scenario import BudgetConfig
from simulation.models.world_state import WorldState


@dataclass
class BudgetUsage:
    ticks: int = 0
    wall_seconds: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    consecutive_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"ticks": self.ticks, "wall_seconds": self.wall_seconds,
                "tokens": self.tokens, "cost_usd": self.cost_usd,
                "consecutive_failures": self.consecutive_failures}


@dataclass
class BudgetViolation:
    kind: str    # ticks | wall_seconds | tokens | cost_usd | failures | extinction
    message: str


class BudgetGuard:
    """Tracks resource consumption and raises StopSimulation when limits hit."""

    def __init__(self, config: BudgetConfig) -> None:
        self.config = config
        self.usage = BudgetUsage()
        self._start_time = time.monotonic()

    def record_tick(self, state: WorldState,
                     tokens_used: int = 0, cost_usd: float = 0.0,
                     failures: int = 0) -> BudgetViolation | None:
        self.usage.ticks += 1
        self.usage.tokens += tokens_used
        self.usage.cost_usd += cost_usd
        self.usage.wall_seconds = time.monotonic() - self._start_time
        if failures > 0:
            self.usage.consecutive_failures += failures
        else:
            self.usage.consecutive_failures = 0

        return self._check(state)

    def _check(self, state: WorldState) -> BudgetViolation | None:
        cfg = self.config
        u = self.usage

        if u.ticks >= cfg.max_ticks:
            return BudgetViolation("ticks", f"tick limit {cfg.max_ticks} reached")
        if u.wall_seconds >= cfg.max_wall_seconds:
            return BudgetViolation("wall_seconds", f"wall time {cfg.max_wall_seconds}s exceeded")
        if u.tokens >= cfg.max_tokens:
            return BudgetViolation("tokens", f"token limit {cfg.max_tokens} reached")
        if u.cost_usd >= cfg.max_cost_usd:
            return BudgetViolation("cost_usd", f"cost limit ${cfg.max_cost_usd} exceeded")
        if u.consecutive_failures >= cfg.max_consecutive_failures:
            return BudgetViolation("failures",
                                    f"{cfg.max_consecutive_failures} consecutive failures")
        if cfg.stop_on_extinction and not state.living_agents():
            return BudgetViolation("extinction", "all agents dead")

        return None

    def remaining(self) -> dict[str, Any]:
        cfg = self.config
        u = self.usage
        return {
            "ticks": cfg.max_ticks - u.ticks,
            "wall_seconds": cfg.max_wall_seconds - u.wall_seconds,
            "tokens": cfg.max_tokens - u.tokens,
            "cost_usd": cfg.max_cost_usd - u.cost_usd,
        }
