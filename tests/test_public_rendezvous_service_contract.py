"""Tests for the standalone public rendezvous service contract."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _use_unpinned_self_hosted_authority(monkeypatch):
    monkeypatch.delenv("RENDEZVOUS_EXPECTED_SIGNING_KEY_ID", raising=False)


@pytest.fixture()
def public_service(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "public-rendezvous.db"))
    monkeypatch.setenv("RENDEZVOUS_SECURITY_SIGNING_SECRET", "test-only-public-rendezvous-signing-secret-32-bytes")
    sys.modules.pop("config", None)
    sys.modules.pop("service", None)
    service = importlib.import_module("service")
    service.reset_state_for_tests()
    yield service
    service.reset_state_for_tests()


def test_list_sessions_returns_owner_and_participant_sessions(public_service):
    session = _create_session(public_service, subject="owner-sub", title="Pairing")
    guest_spki, guest_fp = _device_key()
    public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )

    owner_items = public_service.list_sessions_for_user(requester_user_id=_peer_id("owner-sub"))
    guest_items = public_service.list_sessions_for_user(requester_user_id=_peer_id("guest-sub"))
    stranger_items = public_service.list_sessions_for_user(requester_user_id=_peer_id("stranger-sub"))

    assert [item["id"] for item in owner_items] == [session["id"]]
    assert [item["id"] for item in guest_items] == [session["id"]]
    assert stranger_items == []
    assert owner_items[0]["permissions"]["view_tui"] is False
    assert owner_items[0]["participant_count"] == 1


def test_join_can_be_bound_to_expected_session_id(public_service):
    session = _create_session(public_service, subject="owner-sub")
    guest_spki, guest_fp = _device_key()

    wrong = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
        expected_session_id="different-session",
    )
    right = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
        expected_session_id=session["id"],
    )

    assert wrong == {"ok": False, "reason": "session_not_found"}
    assert right["ok"] is True


def test_idempotent_rejoin_rejects_subject_or_device_key_changes(public_service):
    session = _create_session(public_service, subject="owner-sub")
    guest_spki, guest_fp = _device_key()
    original = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    unchanged = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    changed_subject = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="changed-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    replacement_spki, replacement_fp = _device_key()
    changed_key = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=replacement_fp,
        public_key_spki_b64=replacement_spki,
        oidc_issuer="https://issuer",
    )

    assert original["ok"] is True
    assert unchanged["idempotent"] is True
    assert changed_subject == {"ok": False, "reason": "device_identity_conflict"}
    assert changed_key == {"ok": False, "reason": "device_identity_conflict"}


def test_permission_update_requires_rekey_without_mutating_session(public_service):
    session = _create_session(public_service, subject="owner-sub")
    before = public_service.list_sessions_for_user(requester_user_id=_peer_id("owner-sub"))[0]

    result = public_service.update_session_permissions(
        session_id=session["id"],
        actor_user_id=_peer_id("owner-sub"),
        permissions={"view_tui": True, "remote_control": True},
    )
    forbidden = public_service.update_session_permissions(
        session_id=session["id"],
        actor_user_id=_peer_id("guest-sub"),
        permissions={"view_tui": False},
    )
    after = public_service.list_sessions_for_user(requester_user_id=_peer_id("owner-sub"))[0]

    assert result == {"ok": False, "reason": "permission_update_rekey_required"}
    assert forbidden == {"ok": False, "reason": "forbidden"}
    assert after["permissions"] == before["permissions"]
    assert after["security_epoch"] == before["security_epoch"]


def test_device_identifiers_match_the_signed_package_contract(public_service):
    owner_spki, owner_fp = _device_key()

    with pytest.raises(ValueError, match="device_identity_invalid"):
        public_service.create_session(
            owner_user_id=_peer_id("owner-sub"),
            owner_user_sub="owner-sub",
            owner_device_id="device with spaces",
            owner_device_fingerprint=owner_fp,
            owner_public_key_spki_b64=owner_spki,
            oidc_issuer="https://issuer",
        )

    session = _create_session(public_service, subject="owner-sub")
    result = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="Gerät",
        device_fingerprint=owner_fp,
        public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
    )

    assert result == {"ok": False, "reason": "device_identity_invalid"}


def test_strict_pair_uses_signed_addressed_packages_and_opaque_confirmations(public_service):
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    session = public_service.create_session(
        owner_user_id=_peer_id("alice-sub"),
        owner_user_sub="alice-sub",
        owner_device_id="alice-device",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
    )
    waiting_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=_peer_id("alice-sub"),
    )

    joined = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("bob-sub"),
        user_sub="bob-sub",
        device_id="bob-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    owner_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=_peer_id("alice-sub"),
    )
    guest_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=_peer_id("bob-sub"),
    )

    assert joined["session"]["security_epoch"] == 2
    assert waiting_packages["packages"] == []
    assert waiting_packages["local_package_id"] is None
    assert waiting_packages["hub_key_id"].startswith("rv:")
    assert len(base64.b64decode(waiting_packages["hub_public_key_b64"], validate=True)) == 32
    assert owner_packages["packages"][0]["peer_id"] == _peer_id("bob-sub")
    assert owner_packages["packages"][0]["recipient_peer_id"] == _peer_id("alice-sub")
    assert guest_packages["packages"][0]["peer_id"] == _peer_id("alice-sub")
    assert owner_packages["local_peer_id"] == _peer_id("alice-sub")
    assert owner_packages["local_membership_id"].endswith(":owner")
    assert owner_packages["local_package_id"] == guest_packages["packages"][0]["package_id"]
    assert guest_packages["local_package_id"] == owner_packages["packages"][0]["package_id"]
    assert owner_packages["local_package_id"] != owner_packages["packages"][0]["package_id"]
    assert owner_packages["security_contract_digest"] == guest_packages["security_contract_digest"]
    _verify_package_signature(owner_packages)

    tag = base64.b64encode(b"x" * 32).decode("ascii")
    stored = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=_peer_id("alice-sub"),
        recipient_peer_id=_peer_id("bob-sub"),
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    fetched = public_service.get_key_confirmation(
        session_id=session["id"],
        requester_user_id=_peer_id("bob-sub"),
        sender_peer_id=_peer_id("alice-sub"),
    )
    assert stored["ok"] is True
    assert stored["expires_at_ms"] - stored["created_at_ms"] <= 300_000
    assert fetched["confirmation"]["package_id"] == owner_packages["packages"][0]["package_id"]
    assert fetched["confirmation"]["confirmation_tag"] == tag
    assert fetched["confirmation"]["created_at_ms"] == stored["created_at_ms"]
    assert fetched["confirmation"]["expires_at_ms"] == stored["expires_at_ms"]


def test_confirmation_is_bound_to_direction_package_and_is_immutable(public_service):
    session, owner_packages, guest_packages = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    tag = base64.b64encode(b"a" * 32).decode("ascii")

    wrong_direction = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=guest_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    stored = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    idempotent = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    conflict = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=base64.b64encode(b"b" * 32).decode("ascii"),
    )

    assert wrong_direction == {"ok": False, "reason": "key_package_binding_mismatch"}
    assert stored["ok"] is True
    assert idempotent["idempotent"] is True
    assert idempotent["expires_at_ms"] == stored["expires_at_ms"]
    assert conflict == {"ok": False, "reason": "key_confirmation_conflict"}


def test_confirmation_expires_after_five_minutes(public_service, monkeypatch):
    session, owner_packages, _ = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    tag = base64.b64encode(b"x" * 32).decode("ascii")
    now = public_service._now()
    monkeypatch.setattr(public_service, "_now", lambda: now)
    stored = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )

    monkeypatch.setattr(public_service, "_now", lambda: now + 301)
    fetched = public_service.get_key_confirmation(
        session_id=session["id"],
        requester_user_id=guest_id,
        sender_peer_id=owner_id,
    )

    assert stored["expires_at_ms"] == int((now + 300) * 1000)
    assert fetched == {"ok": True, "confirmation": None}


def test_signaling_poll_is_cursor_based_non_destructive_and_reports_retention_gap(
    public_service,
):
    session, _, _ = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")

    for invalid_cursor in (-1, True, 1 << 63, "0"):
        assert public_service.poll_signals(
            session_id=session["id"],
            user_id=guest_id,
            since=invalid_cursor,
        ) == {"ok": False, "reason": "signal_cursor_invalid"}

    for number in range(public_service._MAX_SIGNAL_QUEUE):
        result = public_service.push_signal(
            session_id=session["id"],
            sender_id=owner_id,
            recipient_id=guest_id,
            signal_type="ice_candidate",
            payload={"candidate": number},
        )
        assert result["sequence"] == str(number + 1)
    rejected = public_service.push_signal(
        session_id=session["id"],
        sender_id=owner_id,
        recipient_id=guest_id,
        signal_type="ice_candidate",
        payload={"candidate": "overflow"},
    )

    first = public_service.poll_signals(session_id=session["id"], user_id=guest_id, since=0)
    replay = public_service.poll_signals(session_id=session["id"], user_id=guest_id, since=0)

    assert rejected == {"ok": False, "reason": "signal_queue_full"}
    assert len(first["signals"]) == public_service._MAX_SIGNAL_QUEUE
    assert [row["sequence"] for row in first["signals"]] == [
        str(number) for number in range(1, public_service._MAX_SIGNAL_QUEUE + 1)
    ]
    assert replay["signals"] == first["signals"]
    assert first["cursor"] == str(public_service._MAX_SIGNAL_QUEUE)
    assert first["cursor_floor"] == "0"
    assert first["truncated"] is False
    assert public_service.poll_signals(
        session_id=session["id"],
        user_id=guest_id,
        since=public_service._MAX_SIGNAL_QUEUE + 1,
    ) == {"ok": False, "reason": "signal_cursor_ahead"}

    # A database upgraded from the former truncating queue can contain a gap.
    # The cursor contract reports it explicitly instead of hiding data loss.
    with public_service._db() as conn:
        conn.execute(
            "DELETE FROM signals WHERE session_id = ? AND recipient_id = ? AND sequence = 1",
            (session["id"], guest_id),
        )
    legacy_gap = public_service.poll_signals(
        session_id=session["id"],
        user_id=guest_id,
        since=0,
    )
    assert legacy_gap["cursor_floor"] == "1"
    assert legacy_gap["truncated"] is True

    acknowledged = public_service.poll_signals(
        session_id=session["id"],
        user_id=guest_id,
        since=public_service._MAX_SIGNAL_QUEUE,
    )
    resumed = public_service.push_signal(
        session_id=session["id"],
        sender_id=owner_id,
        recipient_id=guest_id,
        signal_type="ice_candidate",
        payload={"candidate": "after-ack"},
    )
    after_ack = public_service.poll_signals(
        session_id=session["id"],
        user_id=guest_id,
        since=public_service._MAX_SIGNAL_QUEUE,
    )

    assert acknowledged["signals"] == []
    assert resumed["sequence"] == str(public_service._MAX_SIGNAL_QUEUE + 1)
    assert [row["sequence"] for row in after_ack["signals"]] == [str(public_service._MAX_SIGNAL_QUEUE + 1)]


def test_signal_poll_serializes_bounds_page_and_ack_pruning(public_service):
    session, _, _ = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    for number in range(1, 4):
        assert public_service.push_signal(
            session_id=session["id"],
            sender_id=owner_id,
            recipient_id=guest_id,
            signal_type="ice_candidate",
            payload={"candidate": number},
        )["sequence"] == str(number)

    writer = sqlite3.connect(
        public_service.cfg.RENDEZVOUS_DB_PATH,
        timeout=5,
        isolation_level=None,
        check_same_thread=False,
    )
    writer.execute("PRAGMA busy_timeout = 5000")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "DELETE FROM signals WHERE session_id = ? AND recipient_id = ? AND sequence < 3",
        (session["id"], guest_id),
    )

    started = threading.Event()
    completed = threading.Event()
    results: list[dict] = []
    errors: list[BaseException] = []

    def poll() -> None:
        started.set()
        try:
            results.append(
                public_service.poll_signals(
                    session_id=session["id"],
                    user_id=guest_id,
                    since=0,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted by parent
            errors.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=poll)
    thread.start()
    assert started.wait(timeout=2)
    try:
        assert not completed.wait(timeout=0.2), "poll must wait for the competing cursor writer"
        writer.execute("COMMIT")
    finally:
        if writer.in_transaction:
            writer.execute("ROLLBACK")
        writer.close()
    assert completed.wait(timeout=5)
    thread.join(timeout=2)

    assert errors == []
    assert results[0]["truncated"] is True
    assert results[0]["cursor_floor"] == "2"
    assert [row["sequence"] for row in results[0]["signals"]] == ["3"]


def test_requested_expiry_is_shortened_clamped_and_rejects_invalid(public_service, monkeypatch):
    now = public_service._now()
    monkeypatch.setattr(public_service, "_now", lambda: now)
    short = _create_session(
        public_service,
        subject="short-sub",
        requested_expires_at=now + 120,
    )
    clamped = _create_session(
        public_service,
        subject="clamped-sub",
        requested_expires_at=now + public_service.cfg.SESSION_MAX_DURATION_SECONDS * 2,
    )

    assert short["expires_at"] == now + 120
    assert clamped["expires_at"] == now + public_service.cfg.SESSION_MAX_DURATION_SECONDS
    for invalid in (True, float("nan"), float("inf"), now):
        with pytest.raises(ValueError, match="session_expiry_invalid"):
            _create_session(
                public_service,
                subject=f"invalid-{repr(invalid)}",
                requested_expires_at=invalid,
            )


def test_turn_credentials_require_current_strict_pair_membership(public_service, monkeypatch):
    now = public_service._now()
    monkeypatch.setattr(public_service, "_now", lambda: now)
    monkeypatch.setattr(public_service.cfg, "TURN_SHARED_SECRET", "turn-only-test-secret")
    monkeypatch.setattr(public_service.cfg, "TURN_URLS", ["turn:relay.example:3478"])
    monkeypatch.setattr(public_service.cfg, "TURN_TTL_SECONDS", 3_600)
    session = _create_session(
        public_service,
        subject="owner-sub",
        requested_expires_at=now + 90,
    )
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")

    missing = public_service.issue_turn_credentials(
        session_id="00000000-0000-0000-0000-000000000000",
        requester_user_id=owner_id,
    )
    incomplete = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=owner_id,
    )
    guest_spki, guest_fp = _device_key()
    joined = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=guest_id,
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    owner_credentials = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=owner_id,
    )
    guest_credentials = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=guest_id,
    )
    second_session = _create_session(
        public_service,
        subject="owner-sub",
        requested_expires_at=now + 90,
    )
    second_guest_id = _peer_id("second-guest-sub")
    second_guest_spki, second_guest_fp = _device_key()
    public_service.join_session(
        invite_code=second_session["invite_code"],
        user_id=second_guest_id,
        user_sub="second-guest-sub",
        device_id="second-guest-device",
        device_fingerprint=second_guest_fp,
        public_key_spki_b64=second_guest_spki,
        oidc_issuer="https://issuer",
    )
    second_session_credentials = public_service.issue_turn_credentials(
        session_id=second_session["id"],
        requester_user_id=owner_id,
    )
    stranger = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=_peer_id("stranger-sub"),
    )

    assert missing == {"ok": False, "reason": "session_not_found"}
    assert incomplete == {"ok": False, "reason": "strict_pair_membership_incomplete"}
    assert joined["ok"] is True
    assert owner_credentials["ok"] is True
    assert guest_credentials["ok"] is True
    assert second_session_credentials["ok"] is True
    assert stranger == {"ok": False, "reason": "forbidden"}
    credentials = owner_credentials["credentials"]
    assert credentials["ttl"] == 90
    assert credentials["expires_at"] == int(now) + 90
    session_pseudonym = hmac.new(
        b"turn-only-test-secret",
        b"ananta.turn.session.v1\0" + session["id"].encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    peer_pseudonym = hmac.new(
        b"turn-only-test-secret",
        b"ananta.turn.session-peer.v1\0" + session["id"].encode() + b"\0" + owner_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    assert credentials["username"] == (f"{int(now) + 90}:session-{session_pseudonym}:peer-{peer_pseudonym}")
    usernames = {
        owner_credentials["credentials"]["username"],
        guest_credentials["credentials"]["username"],
        second_session_credentials["credentials"]["username"],
    }
    assert len(usernames) == 3
    for username in usernames:
        assert "oidc:" not in username
        assert owner_id not in username
        assert guest_id not in username
        assert second_guest_id not in username
    assert credentials["session_id"] == session["id"]
    assert credentials["local_peer_id"] == owner_id
    expected_password = base64.b64encode(
        hmac.new(
            b"turn-only-test-secret",
            credentials["username"].encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    assert credentials["password"] == expected_password

    monkeypatch.setattr(public_service, "_now", lambda: now + 91)
    assert public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=owner_id,
    ) == {"ok": False, "reason": "session_inactive"}
    monkeypatch.setattr(public_service, "_now", lambda: now)
    public_service.revoke_session(session_id=session["id"], actor_user_id=owner_id)
    assert public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=owner_id,
    ) == {"ok": False, "reason": "session_inactive"}


def test_legacy_sqlite_schema_migrates_additively_and_fails_closed(monkeypatch, tmp_path):
    database = tmp_path / "legacy-rendezvous.db"
    _create_legacy_database(database)
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(database))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    sys.modules.pop("config", None)
    sys.modules.pop("service", None)
    service = importlib.import_module("service")

    with sqlite3.connect(database) as conn:
        # Emulate two writes by the previous image after a rollback. It omits
        # the additive sequence column, so both rows receive DEFAULT 0.
        conn.execute(
            """INSERT INTO signals (
                   id, session_id, sender_id, recipient_id, signal_type, payload, sent_at
               ) VALUES ('rollback-signal-1', 'legacy-session', 'legacy-owner',
                         'legacy-guest', 'offer', '{}', 3)"""
        )
        conn.execute(
            """INSERT INTO signals (
                   id, session_id, sender_id, recipient_id, signal_type, payload, sent_at
               ) VALUES ('rollback-signal-2', 'legacy-session', 'legacy-owner',
                         'legacy-guest', 'offer', '{}', 4)"""
        )
        session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        migrated_signal_rows = conn.execute(
            "SELECT sequence FROM signals WHERE id IN ('signal-a', 'signal-b') ORDER BY sent_at, rowid"
        ).fetchall()
        rollback_signal_rows = conn.execute(
            "SELECT sequence FROM signals WHERE id LIKE 'rollback-signal-%' ORDER BY id"
        ).fetchall()
        signal_indexes = {row[1] for row in conn.execute("PRAGMA index_list(signals)")}
        confirmation_expiry = conn.execute("SELECT expires_at FROM key_confirmations").fetchone()[0]

    assert "identity_binding_version" in session_columns
    assert migrated_signal_rows == [(1,), (2,)]
    assert rollback_signal_rows == [(0,), (0,)]
    assert "idx_signals_recipient_sequence" not in signal_indexes
    assert confirmation_expiry == 0
    assert service.is_authorized_participant("legacy-session", "legacy-owner") is False


def test_migration_rolls_back_schema_and_cursor_changes_together(public_service, monkeypatch, tmp_path):
    database = tmp_path / "rollback-rendezvous.db"
    _create_legacy_database(database)

    def reject_backfill(_conn):
        raise RuntimeError("injected_backfill_failure")

    monkeypatch.setattr(public_service, "_backfill_signal_sequences", reject_backfill)
    with sqlite3.connect(database, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError, match="injected_backfill_failure"):
            public_service._migrate_database(conn)
        assert conn.in_transaction is False
        session_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(sessions)")}
        signal_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(signals)")}

    assert "identity_binding_version" not in session_columns
    assert "sequence" not in signal_columns


def test_parallel_migrations_serialize_with_cursor_allocation(public_service, monkeypatch, tmp_path):
    database = tmp_path / "parallel-rendezvous.db"
    _create_legacy_database(database)
    original_backfill = public_service._backfill_signal_sequences
    first_backfill_entered = threading.Event()
    release_first_backfill = threading.Event()
    backfill_guard = threading.Lock()
    backfill_calls = 0
    errors: list[BaseException] = []

    def controlled_backfill(conn):
        nonlocal backfill_calls
        with backfill_guard:
            backfill_calls += 1
            is_first = backfill_calls == 1
        if is_first:
            first_backfill_entered.set()
            if not release_first_backfill.wait(timeout=5):
                raise RuntimeError("parallel_migration_test_timeout")
        original_backfill(conn)

    monkeypatch.setattr(public_service, "_backfill_signal_sequences", controlled_backfill)

    def migrate(started: threading.Event, completed: threading.Event) -> None:
        started.set()
        try:
            with sqlite3.connect(database, timeout=5, isolation_level=None) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000")
                public_service._migrate_database(conn)
        except BaseException as exc:  # pragma: no cover - asserted in the parent thread
            errors.append(exc)
        finally:
            completed.set()

    writer_started = threading.Event()
    writer_acquired = threading.Event()
    writer_completed = threading.Event()

    def allocate_cursor() -> None:
        writer_started.set()
        try:
            with sqlite3.connect(database, timeout=5, isolation_level=None) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("BEGIN IMMEDIATE")
                writer_acquired.set()
                row = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) AS cursor FROM signals "
                    "WHERE session_id = ? AND recipient_id = ?",
                    ("legacy-session", "legacy-guest"),
                ).fetchone()
                sequence = int(row["cursor"] or 0) + 1
                conn.execute(
                    """INSERT INTO signals (
                           id, session_id, sender_id, recipient_id, signal_type,
                           payload, sequence, sent_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "parallel-signal",
                        "legacy-session",
                        "legacy-owner",
                        "legacy-guest",
                        "offer",
                        "{}",
                        sequence,
                        3,
                    ),
                )
                conn.execute("COMMIT")
        except BaseException as exc:  # pragma: no cover - asserted in the parent thread
            errors.append(exc)
        finally:
            writer_completed.set()

    first_started = threading.Event()
    first_completed = threading.Event()
    second_started = threading.Event()
    second_completed = threading.Event()
    first = threading.Thread(target=migrate, args=(first_started, first_completed))
    second = threading.Thread(target=migrate, args=(second_started, second_completed))
    writer = threading.Thread(target=allocate_cursor)

    first.start()
    assert first_started.wait(timeout=2)
    assert first_backfill_entered.wait(timeout=2)
    second.start()
    writer.start()
    assert second_started.wait(timeout=2)
    assert writer_started.wait(timeout=2)
    assert second_completed.wait(timeout=0.1) is False
    assert writer_acquired.wait(timeout=0.1) is False

    release_first_backfill.set()
    for thread in (first, second, writer):
        thread.join(timeout=5)
        assert thread.is_alive() is False

    assert errors == []
    assert first_completed.is_set()
    assert second_completed.is_set()
    assert writer_completed.is_set()
    with sqlite3.connect(database) as conn:
        sequences = conn.execute(
            "SELECT sequence FROM signals WHERE session_id = ? AND recipient_id = ? ORDER BY sequence",
            ("legacy-session", "legacy-guest"),
        ).fetchall()
        indexes = {str(row[1]) for row in conn.execute("PRAGMA index_list(signals)")}

    assert sequences == [(1,), (2,), (3,)]
    assert "idx_signals_recipient_sequence" not in indexes


def test_canonical_peer_id_ignores_mutable_display_claims(monkeypatch):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    sys.modules.pop("oidc_auth", None)
    oidc_auth = importlib.import_module("oidc_auth")
    original = oidc_auth.AuthContext(
        sub="stable-sub",
        username="old-name",
        issuer="https://issuer",
        raw={},
    )
    renamed = oidc_auth.AuthContext(
        sub="stable-sub",
        username="new-name",
        issuer="https://issuer/",
        raw={},
    )

    assert original.peer_id == renamed.peer_id == _peer_id("stable-sub")
    assert original.peer_id != oidc_auth.canonical_peer_id("https://other-issuer", "stable-sub")


def test_http_create_binds_actual_issuer_and_returns_local_peer_id(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "http-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="stable-sub",
        username="mutable-display-name",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    public_key, fingerprint = _device_key()
    request_body = {
        "title": "Public pair",
        "security_mode": "strict_e2ee",
        "security_contract_version": 1,
        "mode": "p2p",
        "transport": "webrtc",
        "owner_device_id": "browser-device",
        "owner_device_fingerprint": fingerprint,
        "public_key_spki_b64": public_key,
    }
    client = public_app.app.test_client()

    rejected = client.post(
        "/rendezvous/sessions",
        headers={"Authorization": "Bearer test"},
        json={**request_body, "unexpected": True},
    )
    response = client.post(
        "/rendezvous/sessions",
        headers={"Authorization": "Bearer test"},
        json=request_body,
    )
    oversized = client.post(
        "/rendezvous/sessions",
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        data=json.dumps({**request_body, "title": "x" * (65 * 1024)}),
    )

    payload = response.get_json()
    permission_update = client.patch(
        f"/rendezvous/sessions/{payload['session']['id']}/permissions",
        headers={"Authorization": "Bearer test"},
        json={"permissions": {"view_tui": True}},
    )
    unchanged = public_app.svc.list_sessions_for_user(requester_user_id=context.peer_id)[0]
    assert rejected.status_code == 400
    assert rejected.get_json() == {"error": "request_fields_not_allowed"}
    assert response.status_code == 201
    assert payload["local_peer_id"] == context.peer_id
    assert payload["session"]["local_peer_id"] == context.peer_id
    assert payload["session"]["owner_user_id"] == context.peer_id
    assert payload["session"]["oidc_issuer"] == context.issuer
    assert permission_update.status_code == 409
    assert permission_update.get_json() == {
        "error": "permission_update_rekey_required",
        "reason_code": "permission_update_rekey_required",
    }
    assert unchanged["permissions"]["view_tui"] is False
    assert unchanged["security_epoch"] == payload["session"]["security_epoch"]
    assert oversized.status_code == 413
    assert oversized.get_json() == {"error": "request_too_large"}


def test_join_rate_limit_uses_authenticated_peer_and_ignores_spoofed_xff(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "join-rate-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    contexts = {
        "alice": public_app.AuthContext(
            sub="alice-sub",
            username="alice",
            issuer="https://issuer",
            raw={},
        ),
        "bob": public_app.AuthContext(
            sub="bob-sub",
            username="bob",
            issuer="https://issuer",
            raw={},
        ),
    }
    monkeypatch.setattr(
        public_app,
        "verify_bearer_token",
        lambda header: contexts[header.removeprefix("Bearer ")],
    )
    monkeypatch.setattr(public_app.cfg, "RATE_JOIN_LIMIT", 1)
    with public_app.svc._lock:
        public_app.svc._rate_buckets.clear()
    client = public_app.app.test_client()

    def join(subject: str, spoofed_ip: str):
        return client.post(
            "/rendezvous/sessions/join",
            headers={
                "Authorization": f"Bearer {subject}",
                "X-Forwarded-For": spoofed_ip,
            },
            json={},
            environ_base={"REMOTE_ADDR": "198.51.100.10"},
        )

    alice_first = join("alice", "203.0.113.1")
    bob_first = join("bob", "203.0.113.1")
    alice_spoofed_retry = join("alice", "203.0.113.222")

    assert (alice_first.status_code, alice_first.get_json()) == (
        400,
        {"error": "invite_code_required"},
    )
    assert (bob_first.status_code, bob_first.get_json()) == (
        400,
        {"error": "invite_code_required"},
    )
    assert (alice_spoofed_retry.status_code, alice_spoofed_retry.get_json()) == (
        429,
        {"error": "rate_limited"},
    )


def test_turn_credentials_http_contract_is_session_bound_and_rate_limited(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "turn-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    monkeypatch.setenv("TURN_SHARED_SECRET", "independent-turn-test-secret")
    for module_name in ("config", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    contexts = {
        "owner": public_app.AuthContext(
            sub="owner-sub",
            username="owner",
            issuer="https://issuer",
            raw={},
        ),
        "stranger": public_app.AuthContext(
            sub="stranger-sub",
            username="stranger",
            issuer="https://issuer",
            raw={},
        ),
    }
    monkeypatch.setattr(
        public_app,
        "verify_bearer_token",
        lambda header: contexts[header.removeprefix("Bearer ")],
    )
    waiting_session = _create_session(public_app.svc, subject="owner-sub")
    session, _, _ = _joined_session(public_app.svc)
    client = public_app.app.test_client()
    owner_headers = {"Authorization": "Bearer owner"}
    stranger_headers = {"Authorization": "Bearer stranger"}

    missing = client.get("/rendezvous/turn-credentials", headers=owner_headers)
    invalid = client.get(
        "/rendezvous/turn-credentials?session_id=not-a-uuid",
        headers=owner_headers,
    )
    unknown = client.get(
        "/rendezvous/turn-credentials?session_id=00000000-0000-0000-0000-000000000000",
        headers=owner_headers,
    )
    incomplete = client.get(
        f"/rendezvous/turn-credentials?session_id={waiting_session['id']}",
        headers=owner_headers,
    )
    forbidden = client.get(
        f"/rendezvous/turn-credentials?session_id={session['id']}",
        headers=stranger_headers,
    )
    issued = client.get(
        f"/rendezvous/turn-credentials?session_id={session['id']}",
        headers=owner_headers,
    )

    assert (missing.status_code, missing.get_json()) == (400, {"error": "session_id_required"})
    assert (invalid.status_code, invalid.get_json()) == (400, {"error": "session_id_invalid"})
    assert (unknown.status_code, unknown.get_json()) == (404, {"error": "session_not_found"})
    assert (incomplete.status_code, incomplete.get_json()) == (
        409,
        {"error": "strict_pair_membership_incomplete"},
    )
    assert (forbidden.status_code, forbidden.get_json()) == (403, {"error": "forbidden"})
    assert issued.status_code == 200
    assert issued.headers["Cache-Control"] == "no-store"
    issued_payload = issued.get_json()
    assert issued_payload["session_id"] == session["id"]
    assert issued_payload["local_peer_id"] == contexts["owner"].peer_id
    assert issued_payload["data"]["session_id"] == session["id"]
    assert issued_payload["data"]["local_peer_id"] == contexts["owner"].peer_id
    assert session["id"] not in issued_payload["data"]["username"]

    monkeypatch.setattr(public_app.cfg, "TURN_SHARED_SECRET", "")
    unavailable = client.get(
        f"/rendezvous/turn-credentials?session_id={session['id']}",
        headers=owner_headers,
    )
    assert (unavailable.status_code, unavailable.get_json()) == (503, {"error": "turn_not_configured"})

    monkeypatch.setattr(public_app.cfg, "TURN_SHARED_SECRET", "independent-turn-test-secret")
    monkeypatch.setattr(public_app.cfg, "RATE_TURN_CREDENTIAL_LIMIT", 1)
    with public_app.svc._lock:
        public_app.svc._rate_buckets.clear()
    first = client.get(
        f"/rendezvous/turn-credentials?session_id={session['id']}",
        headers=owner_headers,
    )
    limited = client.get(
        f"/rendezvous/turn-credentials?session_id={session['id']}",
        headers=owner_headers,
    )
    assert first.status_code == 200
    assert (limited.status_code, limited.get_json()) == (429, {"error": "rate_limited"})


def test_signal_poll_http_cursor_is_sqlite_safe_and_rate_limited(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "signal-poll-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="owner-sub",
        username="owner",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    session, _, _ = _joined_session(public_app.svc)
    client = public_app.app.test_client()
    path = f"/webrtc/sessions/{session['id']}/signal"
    headers = {"Authorization": "Bearer owner"}

    invalid_values = (
        "-1",
        "+1",
        " 1",
        "1e3",
        "١",
        "0" * 20,
        str(1 << 63),
        "9" * 5_000,
    )
    for invalid_cursor in invalid_values:
        response = client.get(path, headers=headers, query_string={"since": invalid_cursor})
        assert (response.status_code, response.get_json()) == (
            400,
            {"error": "signal_cursor_invalid"},
        )
    duplicate = client.get(
        path,
        headers=headers,
        query_string=(("since", "0"), ("since", "1")),
    )
    maximum = client.get(
        path,
        headers=headers,
        query_string={"since": str((1 << 63) - 1)},
    )

    assert (duplicate.status_code, duplicate.get_json()) == (
        400,
        {"error": "signal_cursor_invalid"},
    )
    assert (maximum.status_code, maximum.get_json()) == (
        409,
        {"error": "signal_cursor_ahead"},
    )

    monkeypatch.setattr(public_app.cfg, "RATE_SIGNAL_POLL_LIMIT", 1)
    with public_app.svc._lock:
        public_app.svc._rate_buckets.clear()
    first = client.get(path, headers=headers, query_string={"since": "0"})
    limited = client.get(path, headers=headers, query_string={"since": "0"})
    assert first.status_code == 200
    assert (limited.status_code, limited.get_json()) == (429, {"error": "rate_limited"})


def _peer_id(subject: str, issuer: str = "https://issuer") -> str:
    material = b"ananta.public-rendezvous.peer-id.v1\0" + issuer.rstrip("/").encode() + b"\0" + subject.encode()
    return "oidc:" + hashlib.sha256(material).hexdigest()


def _create_session(public_service, *, subject: str, title: str = "Pairing", **kwargs):
    owner_spki, owner_fp = _device_key()
    return public_service.create_session(
        owner_user_id=_peer_id(subject),
        owner_user_sub=subject,
        owner_device_id=f"device-{hashlib.sha256(subject.encode()).hexdigest()[:12]}",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        title=title,
        **kwargs,
    )


def _joined_session(public_service):
    session = _create_session(public_service, subject="owner-sub")
    guest_spki, guest_fp = _device_key()
    joined = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=_peer_id("guest-sub"),
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    assert joined["ok"] is True
    owner_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=_peer_id("owner-sub"),
    )
    guest_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=_peer_id("guest-sub"),
    )
    return session, owner_packages, guest_packages


def _create_legacy_database(database: Path) -> None:
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                owner_user_sub_hash TEXT NOT NULL,
                owner_device_fingerprint TEXT NOT NULL,
                oidc_issuer TEXT NOT NULL,
                title TEXT NOT NULL,
                invite_code TEXT UNIQUE,
                allowed_permissions TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                revoked_at REAL
            );
            CREATE TABLE participants (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_sub_hash TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_fingerprint TEXT NOT NULL,
                permissions TEXT NOT NULL,
                joined_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                revoked_at REAL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE signals (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                sent_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE key_confirmations (
                session_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                sender_peer_id TEXT NOT NULL,
                recipient_peer_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                confirmation_tag TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(session_id, epoch, sender_peer_id, recipient_peer_id),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            INSERT INTO sessions VALUES (
                'legacy-session', 'legacy-owner', 'legacy-hash', 'legacy-fp',
                'https://issuer', 'Legacy', 'LEGACYCODE', '{}', 9999999999, 1, NULL
            );
            INSERT INTO signals VALUES (
                'signal-b', 'legacy-session', 'legacy-owner', 'legacy-guest',
                'offer', '{}', 2
            );
            INSERT INTO signals VALUES (
                'signal-a', 'legacy-session', 'legacy-owner', 'legacy-guest',
                'offer', '{}', 1
            );
            INSERT INTO key_confirmations VALUES (
                'legacy-session', 1, 'legacy-owner', 'legacy-guest',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'legacy-tag', 1
            );
            """
        )


def _device_key() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key

    raw = (
        generate_private_key(SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _verify_package_signature(response: dict) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    package = dict(response["packages"][0])
    signature = base64.b64decode(package.pop("signature_b64"), validate=True)
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(response["hub_public_key_b64"]))
    public_key.verify(signature, canonical)
