"""Durable, exactly-once projection of terminal workflow run traces.

Terminal trace projection used to be best effort: a failure was audited and
the final trace was gone.  A run could therefore end without the view a person
uses to understand what happened ever being written.

Here the pending state is a durable fact on the binding.  A run that reaches a
terminal status is marked pending at a specific runtime revision; the trace is
only cleared when a projection acknowledges exactly that revision.  A restart
resumes, a crash mid-projection retries, and two reconcilers racing the same
run cannot both claim it because the acknowledgement is a compare-and-set on
the revision that was pending when the work started.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

import sqlalchemy as sa

from agent.db_models.workflow_runtime import WorkflowControlBindingDB
from agent.services.workflow_run_history_paging import (
    MAX_WORKFLOW_HISTORY_PAGE,
    WorkflowRunHistoryPagingError,
    page_workflow_run_history,
)

TERMINAL_RUN_STATUSES = frozenset({"cancelled", "completed", "failed", "timed_out"})

_MAX_DRAIN_LIMIT = 256


class WorkflowTerminalTraceError(RuntimeError):
    """Stable fail-closed terminal trace reconciliation error."""


@final
@dataclass(frozen=True, slots=True)
class TerminalTraceCandidate:
    """One run whose terminal trace is still awaiting a projection ACK."""

    tenant_id: str
    workflow_id: str
    run_id: str
    pending_revision: int
    cursor: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "workflow_id", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise WorkflowTerminalTraceError(f"workflow_terminal_trace_{name}_invalid")
        if isinstance(self.pending_revision, bool) or not isinstance(self.pending_revision, int):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_revision_invalid")
        if self.pending_revision < 0 or not isinstance(self.cursor, str):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_revision_invalid")


@runtime_checkable
class WorkflowTerminalTraceStatePort(Protocol):
    """The durable pending state the reconciler is allowed to touch."""

    def mark_pending(self, workflow_id: str, *, revision: int) -> None: ...

    def list_pending(self, *, limit: int) -> tuple[TerminalTraceCandidate, ...]: ...

    def acknowledge(self, workflow_id: str, *, revision: int, cursor: str) -> bool: ...


def is_terminal_status(status: Mapping[str, Any]) -> bool:
    """Report whether a raw runtime status is terminal."""

    value = status.get("status") if isinstance(status, Mapping) else None
    return isinstance(value, str) and value in TERMINAL_RUN_STATUSES


def status_revision(status: Mapping[str, Any]) -> int:
    """Read the runtime revision a terminal status is pending at."""

    value = status.get("revision") if isinstance(status, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTerminalTraceError("workflow_terminal_trace_status_revision_invalid")
    return int(value)


@final
class SQLAlchemyWorkflowTerminalTraceStateStore:
    """Durable pending state stored on the binding row itself.

    The binding is already written transactionally when a run finalizes, so
    marking the trace there costs no second transaction and cannot drift from
    the run it belongs to — which a separate outbox table could.
    """

    __slots__ = ("_clock", "_engine")

    def __init__(self, engine: Any, *, clock: Callable[[], float]) -> None:
        if not callable(clock):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_clock_invalid")
        self._engine = engine
        self._clock = clock

    def mark_pending(self, workflow_id: str, *, revision: int) -> None:
        normalized = _identity(workflow_id, "workflow_id")
        pending_at = _revision(revision)
        with self._engine.begin() as connection:
            result = connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    # Never move a pending marker backwards: an older terminal
                    # observation must not reopen a trace already projected.
                    WorkflowControlBindingDB.trace_projected_revision < pending_at,
                    WorkflowControlBindingDB.trace_pending_revision <= pending_at,
                )
                .values(
                    trace_pending=True,
                    trace_pending_revision=pending_at,
                    updated_at=float(self._clock()),
                )
            )
            del result

    def list_pending(self, *, limit: int) -> tuple[TerminalTraceCandidate, ...]:
        bounded = _limit(limit)
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(
                    WorkflowControlBindingDB.tenant_id,
                    WorkflowControlBindingDB.workflow_id,
                    WorkflowControlBindingDB.run_id,
                    WorkflowControlBindingDB.trace_pending_revision,
                    WorkflowControlBindingDB.trace_cursor,
                )
                .where(WorkflowControlBindingDB.trace_pending.is_(True))
                .order_by(WorkflowControlBindingDB.updated_at)
                .limit(bounded)
            ).all()
        return tuple(
            TerminalTraceCandidate(
                tenant_id=str(row[0]),
                workflow_id=str(row[1]),
                run_id=str(row[2]),
                pending_revision=int(row[3] or 0),
                cursor=str(row[4] or ""),
            )
            for row in rows
        )

    def acknowledge(self, workflow_id: str, *, revision: int, cursor: str) -> bool:
        normalized = _identity(workflow_id, "workflow_id")
        acknowledged = _revision(revision)
        with self._engine.begin() as connection:
            result = connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.trace_pending.is_(True),
                    # The compare-and-set that makes this exactly once: a
                    # reconciler may only clear the revision it observed.
                    WorkflowControlBindingDB.trace_pending_revision == acknowledged,
                )
                .values(
                    trace_pending=False,
                    trace_projected_revision=acknowledged,
                    trace_cursor=str(cursor),
                    updated_at=float(self._clock()),
                )
            )
            return int(result.rowcount or 0) == 1


@final
@dataclass(frozen=True, slots=True)
class TerminalTraceDrainReport:
    """Closed, countable outcome of one bounded reconciliation pass."""

    projected: int
    deferred: int
    failed: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projected": self.projected,
            "deferred": self.deferred,
            "failed": list(self.failed),
        }


@final
class WorkflowTerminalTraceReconciler:
    """Project pending terminal traces exactly once, in bounded passes."""

    __slots__ = ("_history", "_page_size", "_project", "_state")

    def __init__(
        self,
        *,
        state: WorkflowTerminalTraceStatePort,
        history: Callable[[TerminalTraceCandidate, str], Sequence[Mapping[str, Any]]],
        project: Callable[[TerminalTraceCandidate, tuple[dict[str, Any], ...]], None],
        page_size: int = MAX_WORKFLOW_HISTORY_PAGE,
    ) -> None:
        if not isinstance(state, WorkflowTerminalTraceStatePort):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_state_invalid")
        if not callable(history) or not callable(project):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_ports_invalid")
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_page_size_invalid")
        if not 1 <= page_size <= MAX_WORKFLOW_HISTORY_PAGE:
            raise WorkflowTerminalTraceError("workflow_terminal_trace_page_size_invalid")
        self._state = state
        self._history = history
        self._project = project
        self._page_size = int(page_size)

    def drain(self, *, limit: int = 32) -> TerminalTraceDrainReport:
        projected = 0
        deferred = 0
        failed: list[str] = []
        for candidate in self._state.list_pending(limit=_limit(limit)):
            try:
                cursor = self._project_candidate(candidate)
            except Exception:
                # A run whose history cannot be read stays pending: dropping it
                # would silently lose the trace this whole path exists to keep.
                failed.append(candidate.workflow_id)
                continue
            if self._state.acknowledge(
                candidate.workflow_id,
                revision=candidate.pending_revision,
                cursor=cursor,
            ):
                projected += 1
            else:
                # Another reconciler acknowledged the same revision first.  The
                # projection was idempotent, so this is a deferral, not a fault.
                deferred += 1
        return TerminalTraceDrainReport(projected, deferred, tuple(failed))

    def _project_candidate(self, candidate: TerminalTraceCandidate) -> str:
        cursor = candidate.cursor
        while True:
            events = self._history(candidate, cursor)
            try:
                page = page_workflow_run_history(
                    [event for event in events if isinstance(event, Mapping)],
                    after_cursor="",
                    limit=self._page_size,
                )
            except WorkflowRunHistoryPagingError as exc:
                raise WorkflowTerminalTraceError("workflow_terminal_trace_history_invalid") from exc
            if not page.events:
                return cursor
            self._project(candidate, page.events)
            cursor = page.cursor
            if not page.has_more:
                return cursor


@final
@dataclass(frozen=True, slots=True)
class WorkflowTerminalTraceRuntime:
    """The two collaborators the control facade needs, bundled as one seam.

    Marking without draining would accumulate pending traces nothing projects;
    draining without marking would drain a set nothing ever fills.  Bundling
    them makes enabling the trace path a single decision.
    """

    state: WorkflowTerminalTraceStatePort
    reconciler: WorkflowTerminalTraceReconciler

    def __post_init__(self) -> None:
        if not isinstance(self.state, WorkflowTerminalTraceStatePort):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_state_invalid")
        if not isinstance(self.reconciler, WorkflowTerminalTraceReconciler):
            raise WorkflowTerminalTraceError("workflow_terminal_trace_reconciler_invalid")


def build_workflow_terminal_trace_runtime(
    bind: Any,
    *,
    history: Callable[[TerminalTraceCandidate, str], Sequence[Mapping[str, Any]]],
    project: Callable[[TerminalTraceCandidate, tuple[dict[str, Any], ...]], None],
    clock: Callable[[], float] = time.time,
    page_size: int = MAX_WORKFLOW_HISTORY_PAGE,
) -> WorkflowTerminalTraceRuntime:
    """Assemble the durable terminal-trace path against one database bind.

    This is the safe half of the transition cutover: it changes no command
    path.  It only makes the final trace of a terminal run survive a failed
    projection, which is exactly the evidence you want in place *before*
    turning command transitions on.
    """

    state = SQLAlchemyWorkflowTerminalTraceStateStore(bind, clock=clock)
    return WorkflowTerminalTraceRuntime(
        state=state,
        reconciler=WorkflowTerminalTraceReconciler(
            state=state,
            history=history,
            project=project,
            page_size=page_size,
        ),
    )


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise WorkflowTerminalTraceError(f"workflow_terminal_trace_{reason}_invalid")
    return value


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTerminalTraceError("workflow_terminal_trace_revision_invalid")
    return int(value)


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_DRAIN_LIMIT:
        raise WorkflowTerminalTraceError("workflow_terminal_trace_limit_invalid")
    return int(value)


__all__ = [
    "TERMINAL_RUN_STATUSES",
    "SQLAlchemyWorkflowTerminalTraceStateStore",
    "TerminalTraceCandidate",
    "TerminalTraceDrainReport",
    "WorkflowTerminalTraceError",
    "WorkflowTerminalTraceReconciler",
    "WorkflowTerminalTraceRuntime",
    "WorkflowTerminalTraceStatePort",
    "build_workflow_terminal_trace_runtime",
    "is_terminal_status",
    "status_revision",
]
