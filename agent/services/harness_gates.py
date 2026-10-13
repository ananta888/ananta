"""Hard gates for harness candidates (SEH-014).

Gates are registered outside the mutable harness surface. A hard-gate failure
disqualifies a candidate regardless of its fitness; results are structured and
reproducible. Security gates are always hard and cannot be removed or relaxed by
a genome.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GateOutcome:
    gate_id: str
    passed: bool
    reason: str
    hard: bool
    details: Mapping = field(default_factory=dict)

    def to_mapping(self) -> dict:
        return {"gate_id": self.gate_id, "passed": self.passed, "reason": self.reason, "hard": self.hard,
                "details": dict(self.details)}


@dataclass(frozen=True, slots=True)
class GateReport:
    outcomes: tuple[GateOutcome, ...]
    disqualified: bool
    reason: str | None

    def to_mapping(self) -> dict:
        return {"outcomes": [outcome.to_mapping() for outcome in self.outcomes], "disqualified": self.disqualified,
                "reason": self.reason}


@dataclass(frozen=True, slots=True)
class HarnessGate:
    gate_id: str
    hard: bool
    check: Callable[[Mapping], tuple[bool, str]]

    def evaluate(self, candidate: Mapping) -> GateOutcome:
        try:
            passed, reason = self.check(candidate)
        except Exception:
            return GateOutcome(self.gate_id, False, "gate_error", self.hard)
        return GateOutcome(self.gate_id, bool(passed), str(reason), self.hard)


def threshold_gate(gate_id: str, metric: str, minimum: float, *, hard: bool = True) -> HarnessGate:
    def check(candidate: Mapping) -> tuple[bool, str]:
        value = candidate.get(metric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"gate_metric_missing:{metric}"
        return (value >= minimum), ("ok" if value >= minimum else f"gate_below_threshold:{metric}")

    return HarnessGate(gate_id, hard, check)


def security_gate(gate_id: str, metric: str) -> HarnessGate:
    """A security gate must report exactly ``True``; it is always hard."""
    def check(candidate: Mapping) -> tuple[bool, str]:
        return (candidate.get(metric) is True), ("ok" if candidate.get(metric) is True else "gate_security_failed")

    return HarnessGate(gate_id, True, check)


def regression_gate(gate_id: str, metric: str, baseline: float, tolerance: float, *, hard: bool = True) -> HarnessGate:
    def check(candidate: Mapping) -> tuple[bool, str]:
        value = candidate.get(metric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"gate_metric_missing:{metric}"
        if value < baseline - tolerance:
            return False, f"gate_regression:{metric}"
        return True, "ok"

    return HarnessGate(gate_id, hard, check)


@dataclass
class GateEngine:
    gates: Sequence[HarnessGate]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for gate in self.gates:
            if gate.gate_id in seen:
                raise ValueError(f"gate_duplicate:{gate.gate_id}")
            seen.add(gate.gate_id)

    def evaluate(self, candidate: Mapping) -> GateReport:
        outcomes = tuple(gate.evaluate(candidate) for gate in self.gates)
        hard_failures = [outcome for outcome in outcomes if outcome.hard and not outcome.passed]
        disqualified = bool(hard_failures)
        reason = hard_failures[0].reason if hard_failures else None
        return GateReport(outcomes, disqualified, reason)

    def security_gate_ids(self) -> tuple[str, ...]:
        return tuple(gate.gate_id for gate in self.gates if gate.hard and gate.gate_id.startswith("security:"))
