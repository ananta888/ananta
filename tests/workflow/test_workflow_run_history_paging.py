"""Bounded, cursor-stable paging over Hub-owned workflow run history.

The acceptance contract these tests pin: a 600-event history is read in pages
of exactly 256, 256 and 88; the cursor names an event rather than a position;
and an unknown cursor is a fault instead of a silent restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_run_history_paging import (
    MAX_WORKFLOW_HISTORY_PAGE,
    WorkflowRunHistoryPagingError,
    event_cursor,
    page_workflow_run_history,
)


def _events(count: int, *, sequenced: bool = True, start: int = 1) -> list[dict[str, Any]]:
    events = []
    for index in range(start, start + count):
        event: dict[str, Any] = {
            "event_id": f"wfe-{index:06d}",
            "event_type": "workflow.step.completed",
        }
        if sequenced:
            event["sequence"] = index
        events.append(event)
    return events


def test_a_600_event_history_is_read_as_256_256_and_88() -> None:
    history = _events(600)

    sizes: list[int] = []
    cursor = ""
    while True:
        page = page_workflow_run_history(history, after_cursor=cursor)
        sizes.append(len(page.events))
        cursor = page.cursor
        if not page.has_more:
            break

    assert sizes == [256, 256, 88]
    assert sum(sizes) == 600


def test_pages_cover_the_history_exactly_once_and_in_order() -> None:
    history = _events(600)

    seen: list[str] = []
    cursor = ""
    while True:
        page = page_workflow_run_history(history, after_cursor=cursor)
        seen.extend(str(event["event_id"]) for event in page.events)
        cursor = page.cursor
        if not page.has_more:
            break

    assert seen == [str(event["event_id"]) for event in history]
    assert len(set(seen)) == len(seen)


def test_the_path_stays_resumable_far_beyond_ten_thousand_events() -> None:
    history = _events(10_500)

    total = 0
    pages = 0
    cursor = ""
    while True:
        page = page_workflow_run_history(history, after_cursor=cursor)
        total += len(page.events)
        pages += 1
        cursor = page.cursor
        if not page.has_more:
            break

    assert total == 10_500
    assert pages == 42
    assert cursor == event_cursor(history[-1])


def test_the_cursor_follows_the_event_not_its_list_position() -> None:
    """Prepending older events must not shift what the cursor means.

    A list-index cursor would re-read the prepended events as if they were
    new; an identity cursor resumes at the same event it named.
    """

    history = _events(10, start=101)
    first = page_workflow_run_history(history, after_cursor="", limit=4)
    grown = _events(5, start=1) + history

    resumed = page_workflow_run_history(grown, after_cursor=first.cursor, limit=4)

    assert [event["event_id"] for event in resumed.events] == [
        "wfe-000105",
        "wfe-000106",
        "wfe-000107",
        "wfe-000108",
    ]


def test_an_unknown_cursor_fails_closed_instead_of_restarting() -> None:
    history = _events(10)

    with pytest.raises(WorkflowRunHistoryPagingError, match="cursor_unknown"):
        page_workflow_run_history(history, after_cursor="wfe-999999")


def test_unsequenced_events_are_anchored_by_their_stable_event_id() -> None:
    history = _events(5, sequenced=False)

    page = page_workflow_run_history(history, after_cursor="", limit=2)

    assert page.cursor == "wfe-000002"
    assert page.has_more is True
    assert page_workflow_run_history(history, after_cursor=page.cursor, limit=2).events[0]["event_id"] == "wfe-000003"


def test_an_event_without_any_stable_anchor_is_rejected() -> None:
    with pytest.raises(WorkflowRunHistoryPagingError, match="unanchored"):
        event_cursor({"event_type": "workflow.step.completed"})


@pytest.mark.parametrize("limit", (0, -1, MAX_WORKFLOW_HISTORY_PAGE + 1, True, 1.5))
def test_an_out_of_range_page_size_is_rejected(limit: Any) -> None:
    with pytest.raises(WorkflowRunHistoryPagingError, match="limit_invalid"):
        page_workflow_run_history(_events(3), limit=limit)


def test_an_empty_history_yields_an_empty_terminal_page() -> None:
    page = page_workflow_run_history([])

    assert page.events == ()
    assert page.has_more is False
    assert page.cursor == ""


def test_the_last_page_reports_no_more_even_when_it_is_exactly_full() -> None:
    history = _events(MAX_WORKFLOW_HISTORY_PAGE)

    page = page_workflow_run_history(history)

    assert len(page.events) == MAX_WORKFLOW_HISTORY_PAGE
    assert page.has_more is False


def test_page_is_serialisable_for_a_transport_boundary() -> None:
    page = page_workflow_run_history(_events(3), limit=2)

    payload = page.to_dict()

    assert payload["cursor"] == "2"
    assert payload["has_more"] is True
    assert [event["event_id"] for event in payload["events"]] == ["wfe-000001", "wfe-000002"]
