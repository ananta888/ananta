"""SS05.01: Tests für TUI Snapshot/Delta Stream."""
from __future__ import annotations

import os
import time
import uuid
import pytest

from client_surfaces.operator_tui.share_view_policy import ViewSharePolicy
from client_surfaces.operator_tui.share_view_stream import (
    ViewStreamSender,
    ViewStreamReceiver,
    _text_hash,
)


def make_test_key() -> bytes:
    return os.urandom(32)


def test_sender_not_active_by_default():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True)
    sender = ViewStreamSender("sess-1", key, policy)
    assert not sender.is_active


def test_sender_active_after_start():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True)
    sender = ViewStreamSender("sess-1", key, policy)
    sender.start()
    assert sender.is_active


def test_sender_inactive_after_stop():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True)
    sender = ViewStreamSender("sess-1", key, policy)
    sender.start()
    sender.stop()
    assert not sender.is_active


def test_sender_emits_frame_when_active():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=False, redact_notes=False)
    frames = []
    sender = ViewStreamSender("sess-1", key, policy, on_frame=frames.append)
    sender.start()
    sender.tick("hello world\nline2", width=80, height=24)
    assert len(frames) == 1
    assert frames[0]["kind"] == "snapshot"
    assert frames[0]["session_id"] == "sess-1"
    assert frames[0]["encrypted_payload"]


def test_sender_suppresses_frame_when_disabled():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=False)
    frames = []
    sender = ViewStreamSender("sess-1", key, policy, on_frame=frames.append)
    sender.start()
    sender.tick("content", width=80, height=24)
    assert len(frames) == 0


def test_receiver_applies_snapshot():
    key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=False, redact_notes=False)
    frames = []
    sender = ViewStreamSender("sess-1", key, policy, on_frame=frames.append)
    sender.start()
    sender.tick("snapshot content", width=80, height=24)
    assert len(frames) == 1
    receiver = ViewStreamReceiver(key)
    ok = receiver.handle_frame(frames[0])
    assert ok
    assert receiver.current_text == "snapshot content"


def test_receiver_wrong_key_fails():
    good_key = make_test_key()
    bad_key = make_test_key()
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=False, redact_notes=False)
    frames = []
    sender = ViewStreamSender("sess-1", good_key, policy, on_frame=frames.append)
    sender.start()
    sender.tick("content", width=80, height=24)
    receiver = ViewStreamReceiver(bad_key)
    ok = receiver.handle_frame(frames[0])
    assert not ok


def test_receiver_hash_mismatch_marks_stale():
    key = make_test_key()
    receiver = ViewStreamReceiver(key)
    receiver._current_hash = "old_hash_abc"
    from client_surfaces.operator_tui.share_crypto import encrypt_view
    plaintext = b"delta content"
    msg_id = str(uuid.uuid4())
    encrypted = encrypt_view(plaintext, key, msg_id)
    wire = {
        "kind": "delta",
        "session_id": "sess-1",
        "message_id": msg_id,
        "base_hash": "wrong_base_hash",
        "new_hash": _text_hash("delta content"),
        "encrypted_payload": encrypted.to_dict(),
    }
    ok = receiver.handle_frame(wire)
    assert not ok
    assert receiver.needs_resync()


def test_receiver_disconnect_marks_stale():
    key = make_test_key()
    receiver = ViewStreamReceiver(key)
    receiver.mark_disconnected()
    assert receiver.is_stale


def test_text_hash_deterministic():
    h1 = _text_hash("same text")
    h2 = _text_hash("same text")
    assert h1 == h2
    h3 = _text_hash("different text")
    assert h1 != h3
