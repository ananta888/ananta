"""Deterministic, injectable adapters used only by workflow-runtime tests."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.events import InMemoryEventStore
from agent.services.workflow_simulation_service import SimulationFault


class FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self._value = float(initial)

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("fake_clock_reverse_denied")
        self._value += float(seconds)
        return self._value


class ScriptedProvider:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = deque(outcomes)
        self.calls: list[dict[str, Any]] = []

    def invoke(self, request: dict[str, Any]) -> Any:
        self.calls.append(dict(request))
        if not self._outcomes:
            raise RuntimeError("scripted_provider_exhausted")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScriptedTool:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = deque(outcomes)
        self.calls: list[dict[str, Any]] = []

    def execute(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        if not self._outcomes:
            raise RuntimeError("scripted_tool_exhausted")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeArtifactStore:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def put(self, value: Any) -> str:
        digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
        reference = f"artifact://fake/{digest}"
        self._values[reference] = value
        return reference

    def get(self, reference: str) -> Any:
        return self._values[reference]


class FakeApprovalStore:
    def __init__(self, decisions: dict[str, str] | None = None) -> None:
        self._decisions = dict(decisions or {})

    def decide(self, gate_id: str) -> str:
        return self._decisions.get(gate_id, "pending")


class RecordingEventStore(InMemoryEventStore):
    pass


@dataclass(frozen=True)
class ScriptedFaultInjector:
    """Script timeout/crash/interrupt/partial-failure at exact ticks."""

    faults: dict[int, tuple[str, bool]]

    def before_tick(self, *, tick: int, runtime: Any, request: Any) -> None:
        configured = self.faults.get(tick)
        if configured is not None:
            raise SimulationFault(configured[0], recoverable=configured[1])


class DeterministicDeliveryBuffer:
    """Scriptable result reordering without threads or timing races."""

    def __init__(self, order: list[str]) -> None:
        self._order = tuple(order)
        self._values: dict[str, Any] = {}

    def add(self, key: str, value: Any) -> None:
        self._values[str(key)] = value

    def drain(self) -> tuple[Any, ...]:
        unknown = set(self._values) - set(self._order)
        if unknown:
            raise ValueError("delivery_buffer_order_incomplete")
        values = tuple(self._values[key] for key in self._order if key in self._values)
        self._values.clear()
        return values

