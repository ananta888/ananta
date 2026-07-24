from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.services.kanban_event_stream_service import (
    build_kanban_event_stream_service,
)
from ananta_contracts.kanban_events import KanbanEvent


@dataclass
class _Task:
    id: str
    kanban_revision: int
    updated_at: datetime


def _publish(
    service,
    *,
    task_id: str = "task-1",
    revision: int = 1,
    action: str = "kanban.card.moved",
    board_id: str = "hub",
    details: dict | None = None,
):
    task = _Task(
        id=task_id,
        kanban_revision=revision,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return service.publish(
        action=action,
        task=task,
        actor_id="admin",
        details={"board_id": board_id, **(details or {})},
    )


def test_event_contract_is_versioned_strict_and_payload_bounded() -> None:
    event = _publish(build_kanban_event_stream_service()).event
    wire = event.model_dump(mode="json")

    assert wire["schema_version"] == "kanban.event.v1"
    assert KanbanEvent.model_validate(wire) == event
    with pytest.raises(ValidationError):
        KanbanEvent.model_validate({**wire, "callback_url": "http://localhost"})
    with pytest.raises(ValidationError):
        KanbanEvent.model_validate(
            {
                **wire,
                "payload": {
                    f"field_{index}": index for index in range(9)
                },
            }
        )


def test_publish_orders_events_and_deduplicates_same_mutation() -> None:
    service = build_kanban_event_stream_service(max_events_per_board=8)
    first = _publish(service, revision=1)
    duplicate = _publish(service, revision=1)
    second = _publish(service, revision=2)
    third = _publish(
        service,
        task_id="task-2",
        revision=1,
        action="kanban.card.created",
    )

    replay = service.reconnect(board_id="hub", after_sequence=0, limit=100)

    assert first.deduped is False
    assert duplicate.deduped is True
    assert duplicate.event == first.event
    assert second.event.sequence == 2
    assert third.event.sequence == 3
    assert [event.sequence for event in replay.events] == [1, 2, 3]
    assert replay.deduped_events_total == 1
    assert replay.gap_detected is False


def test_payload_is_minimal_and_omits_content_credentials_and_urls() -> None:
    service = build_kanban_event_stream_service()
    result = _publish(
        service,
        details={
            "column_id": "in_progress",
            "position": 2,
            "dependencies": ["one", "two"],
            "body": "<script>alert(1)</script>",
            "callback_url": "http://127.0.0.1",
            "shell_args": ["sh", "-c", "id"],
            "access_token": "secret",
        },
    )

    assert result.event.payload == {
        "column_id": "in_progress",
        "dependency_count": 2,
        "position": 2,
    }


def test_bounded_overflow_detects_gap_and_requires_rest_snapshot() -> None:
    service = build_kanban_event_stream_service(max_events_per_board=2)
    _publish(service, revision=1)
    _publish(service, revision=2)
    _publish(service, revision=3)

    replay = service.reconnect(board_id="hub", after_sequence=0, limit=100)

    assert replay.events == ()
    assert replay.gap_detected is True
    assert replay.gap_reason == "bounded_history_overflow"
    assert replay.overflow_reason == "bounded_history_overflow"
    assert replay.overflow_events_total == 1
    assert replay.snapshot_required is True
    assert replay.snapshot_url == "/api/v1/kanban/boards/hub/snapshot"


def test_reconnect_replays_from_cursor_and_exposes_auth_renewal_contract() -> None:
    service = build_kanban_event_stream_service(max_events_per_board=8)
    _publish(service, revision=1)
    _publish(service, revision=2)
    _publish(service, revision=3)

    replay = service.reconnect(board_id="hub", after_sequence=1, limit=1)

    assert [event.sequence for event in replay.events] == [2]
    assert replay.next_after_sequence == 2
    assert replay.latest_sequence == 3
    assert replay.has_more is True
    assert replay.auth_renewal.mode == "refresh_then_reconnect"
    assert replay.auth_renewal.refresh_endpoint == "/refresh-token"
    assert replay.auth_renewal.resume_header == "Last-Event-ID"
    assert replay.auth_renewal.authorization_header == "Authorization"


def test_client_cursor_ahead_is_a_gap_with_encoded_snapshot_reference() -> None:
    service = build_kanban_event_stream_service(max_events_per_board=8)
    _publish(service, board_id="goal:alpha", revision=1)

    replay = service.reconnect(
        board_id="goal:alpha",
        after_sequence=5,
        limit=100,
    )

    assert replay.gap_reason == "client_sequence_ahead"
    assert replay.snapshot_required is True
    assert replay.snapshot_url == (
        "/api/v1/kanban/boards/goal%3Aalpha/snapshot"
    )
