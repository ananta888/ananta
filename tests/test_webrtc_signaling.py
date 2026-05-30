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


def test_webrtc_signal_send_and_poll(client):
    owner_header = {"Authorization": f"Bearer {_user_jwt('owner-rtc')}"}
    create = client.post(
        "/share-sessions",
        headers={**owner_header, "X-Ananta-Device-Id": "dev-owner-rtc"},
        json={"title": "rtc", "transport": "webrtc", "mode": "relay"},
    )
    assert create.status_code == 201
    data = create.get_json()["data"]
    session_id = data["id"]
    invite_code = data["invite_code"]

    join = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-rtc"},
        json={"invite_code": invite_code, "public_key_fingerprint": "fp:alice"},
    )
    assert join.status_code == 201

    sent = client.post(
        f"/api/webrtc/sessions/{session_id}/signal",
    headers=owner_header,
        json={"recipient_id": "alice", "type": "offer", "payload": {"sdp": "v=0"}},
    )
    assert sent.status_code == 201

    polled = client.get(
        f"/api/webrtc/sessions/{session_id}/signal",
        headers={"Authorization": f"Bearer {_user_jwt('alice')}"},
    )
    assert polled.status_code == 200
    signals = polled.get_json()["data"]["signals"]
    assert len(signals) == 1
    assert signals[0]["type"] == "offer"
    assert signals[0]["sender_id"] == "owner-rtc"


def test_webrtc_signal_limits_queue_depth(client):
    owner_header = {"Authorization": f"Bearer {_user_jwt('owner-queue')}"}
    create = client.post(
        "/share-sessions",
        headers={**owner_header, "X-Ananta-Device-Id": "dev-owner-queue"},
        json={"title": "queue", "transport": "webrtc"},
    )
    assert create.status_code == 201
    data = create.get_json()["data"]
    session_id = data["id"]
    invite_code = data["invite_code"]

    join = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('bob')}", "X-Ananta-Device-Id": "bob-dev"},
        json={"invite_code": invite_code},
    )
    assert join.status_code == 201

    for i in range(25):
        r = client.post(
            f"/api/webrtc/sessions/{session_id}/signal",
            headers=owner_header,
            json={"recipient_id": "bob", "type": "ice_candidate", "payload": {"i": i}},
        )
        assert r.status_code == 201

    poll = client.get(
        f"/api/webrtc/sessions/{session_id}/signal",
        headers={"Authorization": f"Bearer {_user_jwt('bob')}"},
    )
    assert poll.status_code == 200
    signals = poll.get_json()["data"]["signals"]
    assert len(signals) == 20


def test_webrtc_signal_expires_old_entries(client, monkeypatch):
    import agent.routes.webrtc_signaling as mod

    monkeypatch.setattr(mod, "_MAX_SIGNAL_AGE_SECONDS", 0.01)
    mod._signal_queues.clear()
    owner_header = {"Authorization": f"Bearer {_user_jwt('owner-age')}"}

    create = client.post(
        "/share-sessions",
        headers={**owner_header, "X-Ananta-Device-Id": "dev-owner-age"},
        json={"title": "age", "transport": "webrtc"},
    )
    data = create.get_json()["data"]
    session_id = data["id"]
    invite_code = data["invite_code"]

    join = client.post(
        f"/share-sessions/{session_id}/join",
        headers={"Authorization": f"Bearer {_user_jwt('carol')}", "X-Ananta-Device-Id": "carol-dev"},
        json={"invite_code": invite_code},
    )
    assert join.status_code == 201

    sent = client.post(
        f"/api/webrtc/sessions/{session_id}/signal",
        headers=owner_header,
        json={"recipient_id": "carol", "type": "answer", "payload": {"sdp": "v=0"}},
    )
    assert sent.status_code == 201
    time.sleep(0.03)

    poll = client.get(
        f"/api/webrtc/sessions/{session_id}/signal",
        headers={"Authorization": f"Bearer {_user_jwt('carol')}"},
    )
    assert poll.status_code == 200
    assert poll.get_json()["data"]["signals"] == []
