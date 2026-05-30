from __future__ import annotations

from client_surfaces.operator_tui.chat_transport import ChatTransport


def test_enqueue_share_session_message_rejects_notes_and_local_only():
    transport = ChatTransport("http://hub.local", "snake-a", "jwt")
    assert not transport.enqueue_share_session_message(
        {"id": "n1", "channel_type": "notes", "text": "secret"}, share_session_id="sess-1"
    )
    assert not transport.enqueue_share_session_message(
        {"id": "n2", "channel_type": "room", "visibility": "local_only", "text": "secret"},
        share_session_id="sess-1",
    )


def test_enqueue_share_session_message_sets_routing_and_delivery_state():
    transport = ChatTransport("http://hub.local", "snake-a", "jwt")
    ok = transport.enqueue_share_session_message(
        {"id": "m1", "channel_type": "room", "text": "hello"},
        share_session_id="sess-42",
        encrypted_payload={"ciphertext_b64": "abc"},
    )
    assert ok
    outbox = transport.outbox_snapshot()
    assert len(outbox) == 1
    msg = outbox[0]
    assert msg["share_session_id"] == "sess-42"
    assert msg["delivery_state"] == "queued"
    assert msg["_is_encrypted"] is True
    assert "encrypted_payload" in msg
