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
        sender_user_id="user-b",
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
    assert captured[-1][1]["sender_digest"] == captured[-1][1]["owner_digest"]


def test_view_delta_audit_distinguishes_owner_and_participant_sender_without_raw_ids(monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "log_audit", lambda name, payload: captured.append((name, payload)))

    audit.audit_session_created(
        session_id="bilateral-session-private",
        owner_user_id="canonical-owner-private",
        owner_device_id="owner-device-private",
        mode="relay",
        transport="hub_relay",
        permissions={"view_tui": True},
    )
    audit.audit_participant_joined(
        session_id="bilateral-session-private",
        participant_id="participant-membership-private",
        user_id="participant-sender-private",
        device_id="participant-device-private",
        public_key_fingerprint="participant-fingerprint-private",
        permissions={"view_tui": True},
    )
    audit.audit_view_delta_sent(
        session_id="bilateral-session-private",
        owner_user_id="canonical-owner-private",
        sender_user_id="participant-sender-private",
        kind="pair.view_delta",
        new_hash="",
        policy_hash="contract-hash",
    )

    created_payload = captured[0][1]
    participant_payload = captured[1][1]
    delta_payload = captured[2][1]
    assert delta_payload["owner_digest"] == created_payload["owner_digest"]
    assert delta_payload["sender_digest"] == participant_payload["user_digest"]
    assert delta_payload["sender_digest"] != delta_payload["owner_digest"]
    serialized = str(delta_payload)
    assert "canonical-owner-private" not in serialized
    assert "participant-sender-private" not in serialized
    assert "bilateral-session-private" not in serialized


def test_share_audit_pseudonymizes_scope_identity_fingerprint_and_permission_details(monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "log_audit", lambda name, payload: captured.append((name, payload)))
    audit.audit_participant_joined(
        session_id="session-private",
        participant_id="participant-private",
        user_id="user-private",
        device_id="device-private",
        public_key_fingerprint="fingerprint-private",
        permissions={"chat": True, "artifact_share": False},
    )

    details = captured[0][1]
    serialized = str(details)
    for canary in (
        "session-private",
        "participant-private",
        "user-private",
        "device-private",
        "fingerprint-private",
        "artifact_share",
    ):
        assert canary not in serialized
    assert details["granted_permission_count"] == 1
    assert details["permission_count"] == 2
    assert len(details["permission_policy_digest"]) == 64
