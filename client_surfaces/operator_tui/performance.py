from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable


@dataclass(frozen=True)
class PerformanceBudget:
    first_paint_ms: float = 250.0
    command_render_ms: float = 150.0
    section_refresh_ms: float = 500.0


@dataclass(frozen=True)
class Measurement:
    name: str
    elapsed_ms: float
    budget_ms: float

    @property
    def ok(self) -> bool:
        return self.elapsed_ms <= self.budget_ms


def measure(name: str, budget_ms: float, func: Callable[[], object]) -> Measurement:
    start = perf_counter()
    func()
    elapsed = (perf_counter() - start) * 1000.0
    return Measurement(name=name, elapsed_ms=elapsed, budget_ms=budget_ms)
