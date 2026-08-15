"""Stable, bounded paging for Hub-owned workflow run history.

A run's history is the evidence a trace projection is built from, so how it is
paged decides whether that projection can be trusted.  Two properties matter:

* A page is bounded.  An unbounded read turns a long-lived run into an
  unbounded response and an unbounded projection pass.
* A cursor is anchored to the repository's own ordering, never to a position
  in a previously returned list.  A list index silently reinterprets itself
  whenever the underlying sequence changes, which is exactly how a reconciler
  skips or replays events without ever reporting an error.

The event identity is the anchor: an explicit ``sequence`` where the source
provides one, and the stable ``event_id`` otherwise.  A cursor that names an
event the repository does not have is a fault, not a reason to restart at the
beginning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, final

MAX_WORKFLOW_HISTORY_PAGE = 256


class WorkflowRunHistoryPagingError(ValueError):
    """Stable fail-closed history cursor or page error."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowRunHistoryPage:
    """One bounded page plus the cursor that continues it exactly."""

    events: tuple[dict[str, Any], ...]
    cursor: str
    has_more: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [dict(event) for event in self.events],
            "cursor": self.cursor,
            "has_more": self.has_more,
        }


def event_cursor(event: Mapping[str, Any]) -> str:
    """Derive one event's stable cursor anchor."""

    sequence = event.get("sequence")
    if not isinstance(sequence, bool) and isinstance(sequence, int) and sequence > 0:
        return str(sequence)
    event_id = event.get("event_id")
    if isinstance(event_id, str) and event_id:
        return event_id
    raise WorkflowRunHistoryPagingError("workflow_run_history_event_unanchored")


def page_workflow_run_history(
    events: Sequence[Mapping[str, Any]],
    *,
    after_cursor: str = "",
    limit: int = MAX_WORKFLOW_HISTORY_PAGE,
) -> WorkflowRunHistoryPage:
    """Return the bounded page that follows ``after_cursor`` exactly.

    An empty cursor starts at the repository head.  A cursor naming an event
    that is no longer present fails closed rather than silently restarting,
    because a reconciler that restarts has no way to tell a truncated history
    from a corrupted one.
    """

    bounded = _limit(limit)
    ordered = [dict(event) for event in events if isinstance(event, Mapping)]
    start = 0
    if after_cursor:
        anchor = str(after_cursor)
        position = next(
            (index for index, event in enumerate(ordered) if _matches(event, anchor)),
            None,
        )
        if position is None:
            raise WorkflowRunHistoryPagingError("workflow_run_history_cursor_unknown")
        start = position + 1
    window = ordered[start : start + bounded]
    has_more = len(ordered) > start + len(window)
    cursor = event_cursor(window[-1]) if window else str(after_cursor)
    return WorkflowRunHistoryPage(tuple(window), cursor, has_more)


def _matches(event: Mapping[str, Any], anchor: str) -> bool:
    try:
        return event_cursor(event) == anchor
    except WorkflowRunHistoryPagingError:
        return False


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_WORKFLOW_HISTORY_PAGE:
        raise WorkflowRunHistoryPagingError("workflow_run_history_limit_invalid")
    return int(value)


__all__ = [
    "MAX_WORKFLOW_HISTORY_PAGE",
    "WorkflowRunHistoryPage",
    "WorkflowRunHistoryPagingError",
    "event_cursor",
    "page_workflow_run_history",
]
