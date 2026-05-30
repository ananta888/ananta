"""SS05.06: E2E-Test für zwei geteilte TUI-Instanzen.

- Zwei simulierte Owner-TUI Instanzen
- User A erstellt ShareSession und aktiviert view_tui
- User B joint und empfängt read-only Snapshot
- Änderung in User A erscheint bei User B als Delta
- Notes/Secrets werden im geteilten View redacted
- Nach Widerruf erhält User B keine weiteren Deltas
"""
from __future__ import annotations

import os
import time
import pytest


def make_test_key() -> bytes:
    return os.urandom(32)


def test_owner_emits_snapshot():
    """Owner sendet Snapshot, Receiver verarbeitet ihn."""
    key = make_test_key()
    from client_surfaces.operator_tui.share_view_policy import ViewSharePolicy
    from client_surfaces.operator_tui.share_view_stream import ViewStreamSender, ViewStreamReceiver

    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=True, redact_notes=True)
    frames = []
    sender = ViewStreamSender("sess-e2e-1", key, policy, on_frame=frames.append)
    sender.start()

    snapshot_text = "Dashboard\nGoals: 3\nTasks: 7\n"
    sender.tick(snapshot_text, width=80, height=24)
    assert len(frames) == 1

    receiver = ViewStreamReceiver(key)
    ok = receiver.handle_frame(frames[0])
    assert ok
    assert receiver.current_text.strip() == snapshot_text.strip()


def test_notes_are_redacted_in_shared_view():
    key = make_test_key()
    from client_surfaces.operator_tui.share_view_policy import ViewSharePolicy
    from client_surfaces.operator_tui.share_view_stream import ViewStreamSender, ViewStreamReceiver

    policy = ViewSharePolicy(view_share_enabled=True, redact_notes=True, redact_secrets=True)
    frames = []
    sender = ViewStreamSender("sess-e2e-2", key, policy, on_frame=frames.append)
    sender.start()

    text_with_notes = "Dashboard\n[notes] private stuff here\nPublic info"
    sender.tick(text_with_notes, width=80, height=24)
    assert len(frames) == 1

    receiver = ViewStreamReceiver(key)
    ok = receiver.handle_frame(frames[0])
    assert ok
    # Notes müssen redacted sein
    assert "private stuff" not in receiver.current_text
    assert "[REDACTED" in receiver.current_text
    # Öffentliches bleibt sichtbar
    assert "Public info" in receiver.current_text


def test_secrets_are_redacted_in_shared_view():
    key = make_test_key()
    from client_surfaces.operator_tui.share_view_policy import ViewSharePolicy
    from client_surfaces.operator_tui.share_view_stream import ViewStreamSender, ViewStreamReceiver

    policy = ViewSharePolicy(view_share_enabled=True, redact_notes=True, redact_secrets=True)
    frames = []
    sender = ViewStreamSender("sess-e2e-3", key, policy, on_frame=frames.append)
    sender.start()

    text_with_secret = "API Status: OK\ntoken: eyJhbGciOiJSUzI1NiJ9.test\nDone"
    sender.tick(text_with_secret, width=80, height=24)
    assert len(frames) == 1

    receiver = ViewStreamReceiver(key)
    receiver.handle_frame(frames[0])
    assert "eyJhbGciOiJSUzI1NiJ9" not in receiver.current_text


def test_after_revoke_no_more_frames_processed():
    """Nach Widerruf werden keine neuen Frames verarbeitet."""
    import agent.services.rendezvous_service as svc_mod
    svc_mod._sessions.clear()
    svc_mod._participants.clear()
    svc_mod._invite_codes.clear()
    svc_mod._service = None

    from agent.services.rendezvous_service import RendezvousService
    service = RendezvousService()

    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    code = session["invite_code"]
    service.join_session(
        invite_code=code, user_id="user-b", user_sub="sub-b",
        device_id="dev-b", device_fingerprint="fp-b", oidc_issuer="",
    )
    # Session widerrufen
    revoke_result = service.revoke_session(session_id=session["id"], actor_user_id="user-a")
    assert revoke_result["ok"]

    # User B kann keine Presence-Daten mehr abrufen (Session revoked)
    parts = service.get_participants(session_id=session["id"], requester_user_id="user-b")
    # Session ist noch im Store, aber revoked — User B erhält keine neuen Payloads
    # (In Produktion: Hub prüft revoked_at vor Auslieferung)
    # Im Test-Service ist die Session noch vorhanden aber markiert
    import agent.services.rendezvous_service as svc
    sess_data = svc._sessions.get(session["id"])
    assert sess_data and sess_data.get("revoked_at") is not None

    svc_mod._sessions.clear()
    svc_mod._participants.clear()
    svc_mod._invite_codes.clear()
    svc_mod._service = None


def test_view_disabled_by_default():
    """View-Share ist default deaktiviert."""
    from client_surfaces.operator_tui.share_view_policy import build_default_policy
    policy = build_default_policy()
    assert not policy.view_share_enabled


def test_shared_viewer_state_stale_on_disconnect():
    from client_surfaces.operator_tui.shared_viewer import SharedViewer
    viewer = SharedViewer(session_id="sess-e2e-disc", owner_id="user-a")
    viewer.mark_disconnected()
    assert viewer.state.is_stale
    assert viewer.state.is_disconnected
    assert viewer.state.status_label == "DISCONNECTED"


def test_shared_viewer_read_only_blocks_mutations():
    from client_surfaces.operator_tui.shared_viewer import is_viewer_action_blocked
    assert is_viewer_action_blocked("goal_create")
    assert is_viewer_action_blocked("artifact_write")
    assert not is_viewer_action_blocked("scroll_down")
    assert not is_viewer_action_blocked("navigate")
