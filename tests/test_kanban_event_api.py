from __future__ import annotations

import pytest
from werkzeug.security import generate_password_hash

import agent.services.kanban_event_stream_service as event_stream_module
from agent.db_models import UserDB
from agent.repository import user_repo
from agent.services.kanban_event_stream_service import (
    build_kanban_event_stream_service,
)
from agent.services.surface_rate_limit_policy import (
    KANBAN_EVENT_RECONNECT,
    surface_rate_limit_policy,
)


def _enable_kanban(app) -> None:
    app.config["KANBAN_API_ENABLED"] = True
    app.config["KANBAN_WRITE_ENABLED"] = True


@pytest.fixture(autouse=True)
def isolated_event_stream(monkeypatch):
    surface_rate_limit_policy.clear(KANBAN_EVENT_RECONNECT)
    service = build_kanban_event_stream_service(max_events_per_board=128)
    monkeypatch.setattr(
        event_stream_module,
        "_event_stream_service",
        service,
    )
    yield service
    surface_rate_limit_policy.clear(KANBAN_EVENT_RECONNECT)


def _create_board(client, headers, key: str = "event-board") -> str:
    response = client.post(
        "/api/v1/kanban/boards",
        headers=headers,
        json={"scope_type": "hub", "idempotency_key": key},
    )
    assert response.status_code == 201
    return response.get_json()["data"]["id"]


def _create_card(client, headers, board_id: str, key: str = "event-card") -> dict:
    response = client.post(
        f"/api/v1/kanban/boards/{board_id}/cards",
        headers=headers,
        json={"title": "event card", "idempotency_key": key},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def _move(client, headers, board_id: str, card: dict, key: str) -> dict:
    response = client.post(
        f"/api/v1/kanban/cards/{card['id']}/commands/move",
        headers=headers,
        json={
            "board_id": board_id,
            "expected_revision": card["revision"],
            "idempotency_key": key,
            "column_id": "in_progress",
            "position": 0,
        },
    )
    assert response.status_code == 200
    return response.get_json()["data"]


def test_event_api_reconnect_replay_and_auth_contract(
    app,
    client,
    admin_auth_header,
) -> None:
    _enable_kanban(app)
    board_id = _create_board(client, admin_auth_header)
    card = _create_card(client, admin_auth_header, board_id)
    moved = _move(
        client,
        admin_auth_header,
        board_id,
        card,
        "event-move",
    )
    replayed_move = client.post(
        f"/api/v1/kanban/cards/{card['id']}/commands/move",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": card["revision"],
            "idempotency_key": "event-move",
            "column_id": "in_progress",
            "position": 0,
        },
    )
    assert replayed_move.status_code == 200

    response = client.get(
        f"/api/v1/kanban/boards/{board_id}/events?after_sequence=0",
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    batch = response.get_json()["data"]
    assert [event["sequence"] for event in batch["events"]] == [1, 2]
    assert all(event["board_id"] == board_id for event in batch["events"])
    assert all(event["task_id"] == card["id"] for event in batch["events"])
    assert batch["events"][1]["revision"] == moved["revision"]
    assert batch["events"][0]["payload"] == {}
    assert batch["auth_renewal"]["refresh_endpoint"] == "/refresh-token"
    assert batch["auth_renewal"]["resume_header"] == "Last-Event-ID"

    reconnect = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={**admin_auth_header, "Last-Event-ID": "1"},
    )
    assert reconnect.status_code == 200
    assert [
        event["sequence"] for event in reconnect.get_json()["data"]["events"]
    ] == [2]
    repeated = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={**admin_auth_header, "Last-Event-ID": "1"},
    )
    drained = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={**admin_auth_header, "Last-Event-ID": "2"},
    )
    assert repeated.get_json()["data"]["events"] == (
        reconnect.get_json()["data"]["events"]
    )
    assert drained.get_json()["data"]["events"] == []


def test_event_api_reports_overflow_gap_and_rest_snapshot_fallback(
    app,
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    _enable_kanban(app)
    service = build_kanban_event_stream_service(max_events_per_board=2)
    monkeypatch.setattr(
        event_stream_module,
        "_event_stream_service",
        service,
    )
    board_id = _create_board(client, admin_auth_header, "gap-board")
    card = _create_card(
        client,
        admin_auth_header,
        board_id,
        "gap-card",
    )
    moved = _move(
        client,
        admin_auth_header,
        board_id,
        card,
        "gap-move",
    )
    blocked = client.post(
        f"/api/v1/kanban/cards/{card['id']}/commands/block",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": moved["revision"],
            "idempotency_key": "gap-block",
            "reason": "dependency",
        },
    )
    assert blocked.status_code == 200

    response = client.get(
        f"/api/v1/kanban/boards/{board_id}/events?after_sequence=0",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    batch = response.get_json()["data"]
    assert batch["events"] == []
    assert batch["gap_detected"] is True
    assert batch["gap_reason"] == "bounded_history_overflow"
    assert batch["overflow_reason"] == "bounded_history_overflow"
    assert batch["overflow_events_total"] == 1
    assert batch["snapshot_required"] is True
    assert batch["snapshot_url"] == (
        f"/api/v1/kanban/boards/{board_id}/snapshot"
    )
    ahead = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={**admin_auth_header, "Last-Event-ID": "99"},
    )
    ahead_batch = ahead.get_json()["data"]
    assert ahead.status_code == 200
    assert ahead_batch["events"] == []
    assert ahead_batch["gap_detected"] is True
    assert ahead_batch["gap_reason"] == "client_sequence_ahead"
    assert ahead_batch["snapshot_required"] is True


def test_event_api_conceals_hub_board_and_rejects_invalid_cursor(
    app,
    client,
    admin_auth_header,
    user_auth_header,
) -> None:
    _enable_kanban(app)
    board_id = _create_board(client, admin_auth_header, "event-idor-board")

    concealed = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers=user_auth_header,
    )
    invalid = client.get(
        f"/api/v1/kanban/boards/{board_id}/events?after_sequence=not-an-int",
        headers=admin_auth_header,
    )

    assert concealed.status_code == 404
    assert concealed.get_json()["error"]["code"] == "kanban_board_not_found"
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == (
        "kanban_event_cursor_invalid"
    )


def test_event_api_rotates_auth_and_resumes_with_new_access_token(
    app,
    client,
) -> None:
    _enable_kanban(app)
    username = "event-reauth-admin"
    password = "event-reauth-password"
    user_repo.save(
        UserDB(
            username=username,
            password_hash=generate_password_hash(password),
            role="admin",
        )
    )
    login = client.post(
        "/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200
    session = login.get_json()["data"]
    initial_headers = {
        "Authorization": f"Bearer {session['access_token']}"
    }
    board_id = _create_board(
        client,
        initial_headers,
        "event-reauth-board",
    )
    card = _create_card(
        client,
        initial_headers,
        board_id,
        "event-reauth-card",
    )
    initial = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers=initial_headers,
    )
    assert initial.status_code == 200
    assert [
        event["sequence"]
        for event in initial.get_json()["data"]["events"]
    ] == [1]

    forged = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={"Authorization": "Bearer invalid-event-token"},
    )
    assert forged.status_code == 401

    refreshed = client.post(
        "/refresh-token",
        json={"refresh_token": session["refresh_token"]},
    )
    assert refreshed.status_code == 200
    renewed = refreshed.get_json()["data"]
    renewed_headers = {
        "Authorization": f"Bearer {renewed['access_token']}"
    }
    moved = _move(
        client,
        renewed_headers,
        board_id,
        card,
        "event-reauth-move",
    )
    resumed = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers={**renewed_headers, "Last-Event-ID": "1"},
    )
    assert resumed.status_code == 200
    resumed_events = resumed.get_json()["data"]["events"]
    assert [event["sequence"] for event in resumed_events] == [2]
    assert resumed_events[0]["revision"] == moved["revision"]

    replayed_refresh = client.post(
        "/refresh-token",
        json={"refresh_token": session["refresh_token"]},
    )
    assert replayed_refresh.status_code == 401
