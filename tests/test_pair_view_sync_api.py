"""T15: Backend tests for the Pair-Dev view-sync contract.

These tests focus on the additive `view_messages` / `view_cursor`
response fields on /view/poll. The legacy `data.frames` shape is
preserved by the change (covered by test_share_view_transport_api),
so the new contract adds on top of it without breaking clients.
"""
from __future__ import annotations

import time

import jwt


def _user_jwt(username: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {"sub": username, "role": "user", "iat": now, "exp": now + 1800},
        settings.secret_key,
        algorithm="HS256",
    )


def test_view_poll_returns_view_messages_with_envelope_keys(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-envelope"},
        json={"title": "envelope", "permissions": {"view_tui": True, "chat": True}},
    )
    assert created.status_code == 201
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]

    joined = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-envelope"},
        json={"invite_code": invite},
    )
    assert joined.status_code == 201

    pushed = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=admin_auth_header,
        json={
            "message_id": "msg-envelope-1",
            "kind": "delta",
            "width": 120,
            "height": 40,
            "base_hash": "abc",
            "new_hash": "def",
            "encrypted_payload": "STUB1::opaque",
        },
    )
    assert pushed.status_code == 200

    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    assert polled.status_code == 200
    body = polled.get_json()
    # New contract keys
    assert "view_messages" in body
    assert "view_cursor" in body
    msgs = body["view_messages"]
    assert len(msgs) == 1
    first = msgs[0]
    # The envelope shape must be a flat RelayEnvelope: every
    # frontend-relevant key on the top level, encrypted_payload
    # left as a string.
    for key in ("message_id", "kind", "base_hash", "new_hash", "encrypted_payload"):
        assert key in first, f"missing {key} in {first}"
    assert first["message_id"] == "msg-envelope-1"
    assert first["kind"] == "delta"
    assert first["base_hash"] == "abc"
    assert first["new_hash"] == "def"
    assert first["encrypted_payload"] == "STUB1::opaque"
    # Cursor advances to the last delivered message_id
    assert body["view_cursor"] == "msg-envelope-1"
    # Legacy shape is preserved for older clients
    assert "data" in body
    assert "frames" in body["data"]


def test_view_poll_cursor_advances_with_each_message(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-cursor"},
        json={"title": "cursor", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]
    client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-cursor"},
        json={"invite_code": invite},
    )

    for i in range(3):
        client.post(
            f"/share-sessions/{session_id}/view/push",
            headers=admin_auth_header,
            json={
                "message_id": f"m-{i}",
                "kind": "delta",
                "new_hash": f"h-{i}",
                "encrypted_payload": "x",
            },
        )

    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    assert polled.status_code == 200
    body = polled.get_json()
    assert len(body["view_messages"]) == 3
    assert [m["message_id"] for m in body["view_messages"]] == ["m-0", "m-1", "m-2"]
    assert body["view_cursor"] == "m-2"


def test_view_poll_since_cursor_returns_only_newer(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-since"},
        json={"title": "since", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]
    client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-since"},
        json={"invite_code": invite},
    )

    for i in range(3):
        client.post(
            f"/share-sessions/{session_id}/view/push",
            headers=admin_auth_header,
            json={
                "message_id": f"m-{i}",
                "kind": "delta",
                "new_hash": f"h-{i}",
                "encrypted_payload": "x",
            },
        )

    polled1 = client.get(
        f"/share-sessions/{session_id}/view/poll?since=m-1",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    body1 = polled1.get_json()
    assert [m["message_id"] for m in body1["view_messages"]] == ["m-2"]


def test_view_poll_view_messages_empty_when_no_frames(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-empty"},
        json={"title": "empty", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]
    client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-empty"},
        json={"invite_code": invite},
    )
    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    assert polled.status_code == 200
    body = polled.get_json()
    assert body["view_messages"] == []
    assert body["view_cursor"] == ""


def test_view_poll_view_messages_caps_at_ten(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-cap"},
        json={"title": "cap", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]
    client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-cap"},
        json={"invite_code": invite},
    )
    for i in range(15):
        r = client.post(
            f"/share-sessions/{session_id}/view/push",
            headers=admin_auth_header,
            json={
                "message_id": f"m-{i}",
                "kind": "delta",
                "new_hash": f"h-{i}",
                "encrypted_payload": "x",
            },
        )
        if r.status_code == 429:
            break
    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    body = polled.get_json()
    # Backpressure: at most 10 frames delivered per poll
    assert len(body["view_messages"]) <= 10
