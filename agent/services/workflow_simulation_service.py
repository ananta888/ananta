"""Deterministic workflow simulation isolated from production execution.

The service accepts an already constructed runtime and never registers itself as
an execution backend.  Its report is permanently marked ``production_eligible
= False`` so a simulated completion cannot satisfy a production release gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent

SIMULATION_REPORT_SCHEMA = "ananta.workflow_simulation_report.v1"
GOLDEN_TRACE_SCHEMA = "ananta.workflow_golden_trace.v1"
_PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "staging"})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "waiting_for_approval", "paused"})


class SimulationRuntimePort(Protocol):
    runtime_id: str
    runtime_version: str

    def start(self, request: Any) -> Any: ...

    def advance(self, request: Any) -> Any: ...

    def stream(self, request: Any, *, after_sequence: int = 0) -> tuple[CanonicalWorkflowEvent, ...]: ...


class FaultInjectionPort(Protocol):
    def before_tick(self, *, tick: int, runtime: SimulationRuntimePort, request: Any) -> None: ...


@dataclass(frozen=True)
class SimulationFault(RuntimeError):
    fault_type: str
    recoverable: bool = True

    def __str__(self) -> str:
        return self.fault_type


@dataclass(frozen=True)
class SimulationReport:
    runtime_id: str
    runtime_version: str
    terminal_status: str
    ticks: int
    faults: tuple[str, ...]
    golden_trace: dict[str, Any]
    production_eligible: bool = False
    schema: str = SIMULATION_REPORT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "terminal_status": self.terminal_status,
            "ticks": self.ticks,
            "faults": list(self.faults),
            "production_eligible": False,
            "golden_trace": dict(self.golden_trace),
        }


class WorkflowGoldenTraceNormalizer:
    """Normalize only transport identity/time; semantic fields remain exact."""

    normalized_fields = frozenset({"event_id", "occurred_at"})

    def normalize(
        self,
        *,
        runtime_id: str,
        runtime_version: str,
        terminal_status: str,
        events: tuple[CanonicalWorkflowEvent, ...] | list[CanonicalWorkflowEvent],
    ) -> dict[str, Any]:
        normalized_events = []
        for event in sorted(events, key=lambda value: value.sequence):
            raw = event.to_dict()
            normalized_events.append(
                {
                    key: value
                    for key, value in raw.items()
                    if key not in self.normalized_fields
                }
            )
        return {
            "schema": GOLDEN_TRACE_SCHEMA,
            "runtime_id": runtime_id,
            "runtime_version": runtime_version,
            "terminal_status": terminal_status,
            "normalized_fields": sorted(self.normalized_fields),
            "events": normalized_events,
        }

    def canonical_bytes(self, trace: dict[str, Any]) -> bytes:
        return canonical_json(trace).encode("utf-8")


class DeterministicWorkflowSimulationService:
    def __init__(
        self,
        *,
        environment: str,
        explicitly_enabled: bool,
        normalizer: WorkflowGoldenTraceNormalizer | None = None,
    ) -> None:
        normalized_environment = str(environment).strip().lower()
        if normalized_environment in _PRODUCTION_ENVIRONMENTS:
            raise RuntimeError("workflow_simulation_production_forbidden")
        if not explicitly_enabled:
            raise RuntimeError("workflow_simulation_not_enabled")
        self._normalizer = normalizer or WorkflowGoldenTraceNormalizer()

    def run(
        self,
        runtime: SimulationRuntimePort,
        request: Any,
        *,
        max_ticks: int = 100,
        fault_injector: FaultInjectionPort | None = None,
    ) -> SimulationReport:
        if max_ticks < 1:
            raise ValueError("workflow_simulation_tick_limit_invalid")
        result = runtime.start(request)
        faults: list[str] = []
        tick = 1
        while str(result.status) not in _TERMINAL:
            if tick >= max_ticks:
                raise RuntimeError("workflow_simulation_tick_limit_exceeded")
            tick += 1
            try:
                if fault_injector is not None:
                    fault_injector.before_tick(tick=tick, runtime=runtime, request=request)
                result = runtime.advance(request)
            except SimulationFault as fault:
                faults.append(fault.fault_type)
                if not fault.recoverable:
                    result = _TerminalResult("failed")
        terminal_status = str(result.status)
        events = runtime.stream(request, after_sequence=0)
        trace = self._normalizer.normalize(
            runtime_id=f"simulation:{runtime.runtime_id}",
            runtime_version=runtime.runtime_version,
            terminal_status=terminal_status,
            events=events,
        )
        return SimulationReport(
            runtime_id=f"simulation:{runtime.runtime_id}",
            runtime_version=runtime.runtime_version,
            terminal_status=terminal_status,
            ticks=tick,
            faults=tuple(faults),
            golden_trace=trace,
        )


@dataclass(frozen=True)
class _TerminalResult:
    status: str
