from __future__ import annotations

from agent.services import share_audit_service as audit


def test_share_audit_emits_expected_event_names(monkeypatch):
    events: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        events.append((name, payload))

    monkeypatch.setattr(audit, "log_audit", _capture)

    audit.audit_session_created(
        session_id="s1",
        owner_user_id="user-a",
        owner_device_id="dev-a",
        mode="relay",
        transport="hub_relay",
        permissions={"chat": True, "view_tui": False},
    )
    audit.audit_participant_joined(
        session_id="s1",
        participant_id="p1",
        user_id="user-b",
        device_id="dev-b",
        public_key_fingerprint="fp-b",
        permissions={"chat": True},
    )
    audit.audit_permission_changed(
        session_id="s1",
        actor_user_id="user-a",
        new_permissions={"view_tui": True},
    )
    audit.audit_chat_sent(session_id="s1", sender_user_id="user-a", message_id="m1", is_encrypted=True)
    audit.audit_view_started(session_id="s1", owner_user_id="user-a")
    audit.audit_view_delta_sent(
        session_id="s1",
        owner_user_id="user-a",
        kind="delta",
        new_hash="h1",
        policy_hash="p1",
    )
    audit.audit_view_stopped(session_id="s1", owner_user_id="user-a", reason="done")
    audit.audit_participant_revoked(session_id="s1", participant_id="p1", actor_user_id="user-a")

    names = [name for name, _ in events]
    assert "share.session_created" in names
    assert "share.participant_joined" in names
    assert "share.permission_changed" in names
    assert "share.chat_sent" in names
    assert "share.view_started" in names
    assert "share.view_delta_sent" in names
    assert "share.view_stopped" in names
    assert "share.participant_revoked" in names


def test_share_audit_payload_has_no_chat_or_view_cleartext(monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "log_audit", lambda name, payload: captured.append((name, payload)))

    audit.audit_chat_sent(
        session_id="s2",
        sender_user_id="user-a",
        message_id="m2",
        is_encrypted=True,
    )
    audit.audit_view_delta_sent(
        session_id="s2",
        owner_user_id="user-a",
        kind="snapshot",
        new_hash="h2",
        policy_hash="p2",
    )
    payload_text = str(captured)
    assert "hello" not in payload_text.lower()
    assert "ciphertext" not in payload_text.lower()
    assert "screen" not in payload_text.lower()
