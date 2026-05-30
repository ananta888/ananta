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


def test_share_view_push_and_poll_allowed(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-view"},
        json={"title": "view", "permissions": {"view_tui": True, "chat": True}},
    )
    assert created.status_code == 201
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]

    joined = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-view"},
        json={"invite_code": invite},
    )
    assert joined.status_code == 201

    pushed = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=admin_auth_header,
        json={
            "message_id": "m1",
            "kind": "snapshot",
            "width": 120,
            "height": 40,
            "new_hash": "h1",
            "encrypted_payload": {"ciphertext_b64": "abc", "nonce_b64": "n"},
        },
    )
    assert pushed.status_code == 200

    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    assert polled.status_code == 200
    frames = polled.get_json()["data"]["frames"]
    assert len(frames) == 1
    assert frames[0]["message_id"] == "m1"


def test_share_view_denied_without_permission(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-no-view"},
        json={"title": "no-view", "permissions": {"view_tui": False, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]

    pushed = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=admin_auth_header,
        json={"message_id": "m1", "encrypted_payload": {"ciphertext_b64": "abc"}},
    )
    assert pushed.status_code == 403
    assert pushed.get_json()["error"] == "view_tui_permission_required"


def test_share_view_poll_denied_after_participant_revoked(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-revoke"},
        json={"title": "revoke", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]

    joined = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('bob')}", "X-Ananta-Device-Id": "bob-view"},
        json={"invite_code": invite},
    )
    participant_id = joined.get_json()["data"]["id"]

    revoked = client.delete(
        f"/share-sessions/{session_id}/participants/{participant_id}",
        headers=admin_auth_header,
    )
    assert revoked.status_code == 200

    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('bob')}"},
    )
    assert polled.status_code == 403
    assert polled.get_json()["error"] == "not_a_participant"


def test_share_view_poll_denied_for_expired_session(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-expired"},
        json={
            "title": "expired",
            "permissions": {"view_tui": True, "chat": True},
            "expires_at": time.time() - 1.0,
        },
    )
    session_id = created.get_json()["data"]["id"]

    polled = client.get(f"/share-sessions/{session_id}/view/poll", headers=admin_auth_header)
    assert polled.status_code == 403
    assert polled.get_json()["error"] == "session_not_active"


def test_share_view_backpressure_limits_delivered_frames(client, admin_auth_header):
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-bp"},
        json={"title": "bp", "permissions": {"view_tui": True, "chat": True}},
    )
    session = created.get_json()["data"]
    session_id = session["id"]
    invite = session["invite_code"]

    joined = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('carol')}", "X-Ananta-Device-Id": "carol-view"},
        json={"invite_code": invite},
    )
    assert joined.status_code == 201

    for i in range(70):
        pushed = client.post(
            f"/share-sessions/{session_id}/view/push",
            headers=admin_auth_header,
            json={
                "message_id": f"m{i}",
                "kind": "delta",
                "new_hash": f"h{i}",
                "encrypted_payload": {"ciphertext_b64": "abc"},
            },
        )
        if pushed.status_code == 429:
            break
        assert pushed.status_code == 200

    polled = client.get(
        f"/share-sessions/{session_id}/view/poll",
        headers={"Authorization": f"Bearer {_user_jwt('carol')}"},
    )
    assert polled.status_code == 200
    frames = polled.get_json()["data"]["frames"]
    assert len(frames) <= 10
