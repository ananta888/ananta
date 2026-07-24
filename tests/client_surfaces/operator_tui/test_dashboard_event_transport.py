from __future__ import annotations

import json

import pytest

from client_surfaces.operator_tui.dashboard_event_transport import (
    KanbanEventBatch,
    KanbanEventContractError,
    parse_sse_payload,
)


def _event(sequence: int, *, board_id: str = "hub") -> dict:
    return {
        "schema_version": "kanban.event.v1",
        "event_id": str(sequence),
        "board_id": board_id,
        "task_id": "TASK-1",
        "revision": sequence,
        "sequence": sequence,
        "event_type": "kanban.card.moved",
        "occurred_at": "2026-07-23T12:00:00Z",
        "payload": {"column_id": "in_progress", "position": 0},
    }


def test_json_batch_contract_is_bounded_and_strict() -> None:
    batch = KanbanEventBatch.from_mapping(
        {
            "data": {
                "events": [_event(1), _event(2)],
                "next_after_sequence": 2,
                "latest_sequence": 2,
                "has_more": False,
            }
        },
        after_sequence=0,
        max_events=2,
    )

    assert [event.sequence for event in batch.events] == [1, 2]
    assert batch.latest_sequence == 2
    with pytest.raises(
        KanbanEventContractError,
        match="kanban_event_batch_size_invalid",
    ):
        KanbanEventBatch.from_mapping(
            {"events": [_event(1), _event(2), _event(3)]},
            after_sequence=0,
            max_events=2,
        )


def test_sse_event_uses_last_event_id_without_weakening_event_contract() -> None:
    event = _event(1)
    event.pop("event_id")
    raw = b"id: 1\nevent: kanban\n" + (
        b"data: " + json.dumps(event).encode("utf-8") + b"\n\n"
    )

    batch = parse_sse_payload(
        raw,
        after_sequence=0,
        max_events=10,
        max_event_bytes=4096,
    )

    assert len(batch.events) == 1
    assert batch.events[0].event_id == "1"
    assert batch.next_after_sequence == 1


def test_sse_snapshot_control_batch_is_preserved() -> None:
    control = {
        "events": [],
        "gap_detected": True,
        "gap_reason": "bounded_history_overflow",
        "overflow_reason": "bounded_history_overflow",
        "snapshot_required": True,
        "snapshot_url": "/api/v1/kanban/boards/hub",
        "next_after_sequence": 4,
        "latest_sequence": 7,
        "has_more": False,
    }
    raw = b"data: " + json.dumps(control).encode("utf-8") + b"\n\n"

    batch = parse_sse_payload(
        raw,
        after_sequence=4,
        max_events=10,
        max_event_bytes=4096,
    )

    assert batch.snapshot_required is True
    assert batch.latest_sequence == 7
    assert batch.overflow_reason == "bounded_history_overflow"


def test_sse_rejects_oversized_and_malformed_frames() -> None:
    with pytest.raises(
        KanbanEventContractError,
        match="kanban_event_frame_too_large",
    ):
        parse_sse_payload(
            b"data: " + b"x" * 33 + b"\n\n",
            after_sequence=0,
            max_events=1,
            max_event_bytes=32,
        )
    with pytest.raises(
        KanbanEventContractError,
        match="kanban_event_sse_data_invalid",
    ):
        parse_sse_payload(
            b"data: not-json\n\n",
            after_sequence=0,
            max_events=1,
            max_event_bytes=64,
        )
