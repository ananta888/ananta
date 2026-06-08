"""Run-State model for Visual Process execution (VPAD-011).

Tracks which steps are pending / running / done / failed during a
process run. This is the read-model that the Angular dashboard and TUI
poll to show live execution progress.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

StepRunStatus = Literal["pending", "running", "done", "failed", "skipped", "blocked"]


@dataclass
class StepRunState:
    step_id: str
    status: StepRunStatus = "pending"
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    output_artifacts: dict[str, Any] = field(default_factory=dict)

    def start(self) -> None:
        self.status = "running"
        self.started_at = time.time()

    def complete(self, artifacts: dict[str, Any] | None = None) -> None:
        self.status = "done"
        self.finished_at = time.time()
        if artifacts:
            self.output_artifacts.update(artifacts)

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.finished_at = time.time()
        self.error = error

    def skip(self) -> None:
        self.status = "skipped"
        self.finished_at = time.time()

    def duration_s(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 3)
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s(),
            "error": self.error,
            "output_artifacts": list(self.output_artifacts.keys()),
        }


@dataclass
class ProcessRunState:
    """Full run state for one process execution."""
    run_id: str
    graph_id: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    step_states: dict[str, StepRunState] = field(default_factory=dict)

    def init_steps(self, step_ids: list[str]) -> None:
        for sid in step_ids:
            if sid not in self.step_states:
                self.step_states[sid] = StepRunState(step_id=sid)

    def get_step(self, step_id: str) -> StepRunState | None:
        return self.step_states.get(step_id)

    def overall_status(self) -> StepRunStatus:
        statuses = {s.status for s in self.step_states.values()}
        if "failed" in statuses:
            return "failed"
        if "running" in statuses:
            return "running"
        if all(s in ("done", "skipped") for s in statuses):
            return "done"
        return "pending"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graph_id": self.graph_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status(),
            "steps": {sid: s.as_dict() for sid, s in self.step_states.items()},
        }
