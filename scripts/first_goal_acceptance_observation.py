"""Polling observation state of one first-goal acceptance run.

``run_once`` polls the Hub until the goal terminalizes; this module owns the
state that polling accumulates (status timeline, task materialization,
deadlock and circuit-breaker windows, workspace writes, verification and
LLM activity, idle stretches). Keeping it separate lets the runner stay a
thin loop over Hub reads (SRP) and makes each observation rule testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "aborted", "timeout"}
ACTIVE_STATUSES = {"assigned", "proposing", "in_progress", "running"}
BLOCKED_SET = {"todo", "blocked_by_dependency"}
PLANNING_STATUSES = {"planning", "planning_queued", "planning_running", "planned"}
_POST_ASSIGNED_STATUSES = {"proposing", "in_progress", "completed", "failed"}
_VERIFICATION_OUTPUT_MARKERS = ("pytest", "test", "verification", "smoke", "nicht-ausfuehrbar", "nicht ausführbar")


@dataclass
class GoalRunObservation:
    """Everything the acceptance criteria need from the polling loop."""

    started_at: float
    max_circuit_breaker_open_seconds: int
    status_seen: list[tuple[float, str]] = field(default_factory=list)
    first_status_time: float | None = None
    task_count_at_60: int = 0
    first_task_seen_at: float | None = None
    first_assigned_at: float | None = None
    first_post_assigned_change: bool = False
    deadlock_start: float | None = None
    deadlock_violation: bool = False
    cb_open_start: float | None = None
    cb_open_violation: bool = False
    workspace_file_seen: bool = False
    verification_seen: bool = False
    max_idle_stretch_s: float = 0.0
    idle_hang_violation: bool = False
    last_activity_at: float = 0.0
    prev_status: str | None = None
    prev_task_count: int = 0
    planning_extended_by_activity: bool = False
    planning_real_llm_seen: bool = False
    planning_synthetic_llm_seen: bool = False

    def __post_init__(self) -> None:
        self.last_activity_at = self.started_at

    def observe_poll(
        self,
        now: float,
        *,
        status: str,
        tasks: list[dict[str, Any]],
        host_dir: Path,
        autopilot_status: Callable[[], dict[str, Any]],
        task_detail: Callable[[str], dict[str, Any]],
    ) -> None:
        """Fold one poll of the goal detail into the observation (Hub reads in the original order)."""

        self._observe_goal_status(now, status)
        task_statuses = self._observe_task_list(now, tasks)
        self._observe_deadlock(now, task_statuses)
        self._observe_circuit_breakers(now, autopilot_status())
        if host_dir.exists() and any(p.is_file() for p in host_dir.rglob("*")):
            self.workspace_file_seen = True
        self._observe_task_details(now, tasks, task_detail)
        self._observe_idle(now, status)

    def _observe_goal_status(self, now: float, status: str) -> None:
        if not status:
            return
        self.status_seen.append((now, status))
        if self.first_status_time is None and status in PLANNING_STATUSES:
            self.first_status_time = now
        if self.prev_status is None or status != self.prev_status:
            self.last_activity_at = now
        self.prev_status = status

    def _observe_task_list(self, now: float, tasks: list[dict[str, Any]]) -> list[str]:
        if now - self.started_at <= 60:
            self.task_count_at_60 = max(self.task_count_at_60, len(tasks))
        if self.first_task_seen_at is None and len(tasks) > 0:
            self.first_task_seen_at = now
            self.last_activity_at = now
        if len(tasks) != self.prev_task_count:
            self.last_activity_at = now
            self.prev_task_count = len(tasks)
        task_statuses = [str(t.get("status") or "") for t in tasks]
        if any(s == "assigned" for s in task_statuses) and self.first_assigned_at is None:
            self.first_assigned_at = now
            self.last_activity_at = now
        if self.first_assigned_at is not None and any(s in _POST_ASSIGNED_STATUSES for s in task_statuses):
            self.first_post_assigned_change = True
            self.last_activity_at = now
        return task_statuses

    def _observe_deadlock(self, now: float, task_statuses: list[str]) -> None:
        non_terminal = [s for s in task_statuses if s and s not in TERMINAL_STATUSES]
        if not (non_terminal and all(s in BLOCKED_SET for s in non_terminal)):
            self.deadlock_start = None
        elif self.deadlock_start is None:
            self.deadlock_start = now
        elif now - self.deadlock_start > 120:
            self.deadlock_violation = True

    def _observe_circuit_breakers(self, now: float, autopilot: dict[str, Any]) -> None:
        open_count = int((autopilot.get("circuit_breakers") or {}).get("open_count") or 0)
        if open_count <= 0:
            self.cb_open_start = None
        elif self.cb_open_start is None:
            self.cb_open_start = now
        elif now - self.cb_open_start > self.max_circuit_breaker_open_seconds:
            self.cb_open_violation = True

    def _observe_task_details(
        self,
        now: float,
        tasks: list[dict[str, Any]],
        task_detail: Callable[[str], dict[str, Any]],
    ) -> None:
        for t in tasks:
            tid = str(t.get("id") or "").strip()
            if not tid:
                continue
            detail = task_detail(tid)
            if _shows_verification(detail):
                self.verification_seen = True
                break
            self._observe_llm_call_profile(now, detail)

    def _observe_llm_call_profile(self, now: float, detail: dict[str, Any]) -> None:
        proposal = dict(detail.get("last_proposal") or {})
        cli_result = dict(proposal.get("cli_result") or {})
        prof_entries = list(cli_result.get("llm_call_profile") or [])
        if prof_entries:
            self.last_activity_at = now
        for entry in prof_entries:
            if not isinstance(entry, dict):
                continue
            est = bool(entry.get("estimated"))
            src = str(entry.get("source") or "").strip()
            if est or src == "orchestrator_synthetic":
                self.planning_synthetic_llm_seen = True
            else:
                self.planning_real_llm_seen = True

    def _observe_idle(self, now: float, status: str) -> None:
        idle_s = now - self.last_activity_at
        self.max_idle_stretch_s = max(self.max_idle_stretch_s, idle_s)
        if status in {"planning", "planned"} and idle_s > 120:
            self.idle_hang_violation = True
        if (
            status in PLANNING_STATUSES
            and (now - self.started_at) > 60
            and (self.planning_real_llm_seen or self.first_task_seen_at is not None)
        ):
            self.planning_extended_by_activity = True

    def seconds_since_start(self, moment: float | None) -> float | None:
        return (moment - self.started_at) if moment else None


def _shows_verification(detail: dict[str, Any]) -> bool:
    if detail.get("verification_status"):
        return True
    last_output = str(detail.get("last_output") or "")
    return any(k in last_output.lower() for k in _VERIFICATION_OUTPUT_MARKERS)


def early_analysis(status: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    stuck = status in {"planning", "planning_queued", "planning_running"} and not tasks
    return {
        "mode": "early_exit",
        "classification": "planning_stuck" if stuck else "progressing",
        "status": status,
        "task_count": len(tasks),
    }
