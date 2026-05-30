from __future__ import annotations

import time

import jwt


def _user_jwt(username: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "role": "user",
            "iat": now,
            "exp": now + 1800,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def test_share_sessions_create_list_join_permissions_and_revoke(client, admin_auth_header):
    create = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "dev-owner-1"},
        json={"title": "team debug", "mode": "relay", "transport": "hub_relay"},
    )
    assert create.status_code == 201
    payload = create.get_json()["data"]
    assert payload["permissions"]["chat"] is True
    assert payload["permissions"]["view_tui"] is False
    assert payload["permissions"]["remote_control"] is False
    assert payload["invite_code"]
    session_id = payload["id"]

    listed = client.get("/share-sessions", headers=admin_auth_header)
    assert listed.status_code == 200
    listed_ids = [item["id"] for item in listed.get_json()["data"]["items"]]
    assert session_id in listed_ids

    no_invite = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
        json={"device_id": "alice-dev-1", "invite_code": "wrong"},
    )
    assert no_invite.status_code == 403
    assert no_invite.get_json()["error"] == "invalid_invite"

    joined = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-dev-1"},
        json={"invite_code": payload["invite_code"], "public_key_fingerprint": "fp:alice:001"},
    )
    assert joined.status_code == 201
    participant = joined.get_json()["data"]
    assert participant["session_id"] == session_id
    assert participant["user_id"] == "alice"
    participant_id = participant["id"]

    forbidden_change = client.patch(
        f"/share-sessions/{session_id}/permissions",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
        json={"permissions": {"view_tui": True}},
    )
    assert forbidden_change.status_code == 403

    changed = client.patch(
        f"/share-sessions/{session_id}/permissions",
        headers=admin_auth_header,
        json={"permissions": {"chat": True, "view_tui": True, "remote_control": False}},
    )
    assert changed.status_code == 200
    updated = changed.get_json()["data"]["permissions"]
    assert updated["chat"] is True
    assert updated["view_tui"] is True
    assert updated["remote_control"] is False

    revoked = client.delete(
        f"/share-sessions/{session_id}/participants/{participant_id}",
        headers=admin_auth_header,
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["data"]["revoked_at"] is not None


def test_share_sessions_join_requires_oidc_context(client, admin_auth_header):
    create = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "dev-owner-2"},
        json={"title": "oidc gate"},
    )
    assert create.status_code == 201
    session_id = create.get_json()["data"]["id"]
    invite_code = create.get_json()["data"]["invite_code"]

    # token without `sub` should fail OIDC identity gate
    from agent.config import settings

    now = int(time.time())
    weak_token = jwt.encode(
        {"username": "legacy-user", "role": "user", "iat": now, "exp": now + 1800},
        settings.secret_key,
        algorithm="HS256",
    )
    response = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {weak_token}"},
        json={"device_id": "legacy-dev", "invite_code": invite_code},
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "oidc_context_required"
