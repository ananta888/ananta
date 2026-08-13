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
    assert owner_items[0]["owner_user_id"] == _peer_id("owner-sub")
    assert owner_items[0]["invite_code"] == session["invite_code"]
    assert owner_items[0]["participants"][0]["user_id"] == _peer_id("guest-sub")
    assert owner_items[0]["local_role"] == "owner"
    assert guest_items[0]["participants"][0]["user_id"] == _peer_id("guest-sub")
    assert guest_items[0]["local_role"] == "participant"


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


def test_v2_same_account_pair_is_device_addressed_and_capability_isolated(public_service, monkeypatch):
    account_id = _peer_id("shared-sub")
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    owner_capability = "A" * 43
    guest_capability = "B" * 43
    expires_at = public_service._now() + 600
    session = public_service.create_session(
        owner_user_id=account_id,
        owner_user_sub="shared-sub",
        owner_device_id="owner-device",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        requested_expires_at=expires_at,
        identity_binding_version=2,
        membership_capability=owner_capability,
    )
    joined = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=account_id,
        user_sub="shared-sub",
        device_id="guest-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
        expected_identity_binding_version=2,
        membership_capability=guest_capability,
    )

    owner_peer_id = session["owner_peer_id"]
    guest_peer_id = joined["participant"]["peer_id"]
    assert session["identity_binding_version"] == 2
    assert owner_peer_id.startswith("peer:")
    assert guest_peer_id.startswith("peer:")
    assert owner_peer_id != guest_peer_id
    assert joined["participant"]["account_id"] == account_id
    assert "membership_capability" not in session
    assert "membership_capability" not in joined

    unproven = public_service.list_sessions_for_user(requester_user_id=account_id)
    owner_selected = public_service.list_sessions_for_membership_proofs(
        requester_user_id=account_id,
        membership_proofs=[
            {
                "session_id": session["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": owner_capability,
            }
        ],
    )[0]
    guest_selected = public_service.list_sessions_for_membership_proofs(
        requester_user_id=account_id,
        membership_proofs=[
            {
                "session_id": session["id"],
                "local_peer_id": guest_peer_id,
                "membership_capability": guest_capability,
            }
        ],
    )[0]
    forged_proofs = public_service.list_sessions_for_membership_proofs(
        requester_user_id=account_id,
        membership_proofs=[
            {
                "session_id": session["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": guest_capability,
            },
            {
                "session_id": session["id"],
                "local_peer_id": guest_peer_id,
                "membership_capability": owner_capability,
            },
        ],
    )
    assert unproven == []
    assert forged_proofs == []
    assert owner_selected["local_peer_id"] == owner_peer_id
    assert owner_selected["local_peer_ids"] == [owner_peer_id]
    assert owner_selected["local_role"] == "owner"
    assert owner_selected["invite_code"] == session["invite_code"]
    assert guest_selected["local_peer_id"] == guest_peer_id
    assert guest_selected["local_peer_ids"] == [guest_peer_id]
    assert guest_selected["local_role"] == "participant"
    assert "invite_code" not in guest_selected
    redacted_fields = {
        "participants",
        "owner_user_id",
        "owner_user_sub_hash",
        "owner_account_id",
        "owner_device_id",
        "owner_device_fingerprint",
        "owner_public_key_spki_b64",
        "oidc_issuer",
    }
    assert redacted_fields.isdisjoint(owner_selected)
    assert redacted_fields.isdisjoint(guest_selected)
    assert owner_capability not in json.dumps(owner_selected)
    assert guest_capability not in json.dumps(guest_selected)
    with public_service._db() as conn:
        stored_session = conn.execute(
            """SELECT owner_membership_capability_hash,
                      owner_capability_lookup_hash,
                      owner_create_request_hash
               FROM sessions WHERE id = ?""",
            (session["id"],),
        ).fetchone()
        stored_participant = conn.execute(
            "SELECT membership_capability_hash FROM participants WHERE session_id = ?",
            (session["id"],),
        ).fetchone()
        raw_rows = (
            tuple(conn.execute("SELECT * FROM sessions WHERE id = ?", (session["id"],)).fetchone()),
            tuple(
                conn.execute(
                    "SELECT * FROM participants WHERE session_id = ?",
                    (session["id"],),
                ).fetchone()
            ),
        )
    stored_hashes = [*stored_session, stored_participant[0]]
    assert all(len(value) == 64 and all(char in "0123456789abcdef" for char in value) for value in stored_hashes)
    assert owner_capability not in repr(stored_hashes)
    assert guest_capability not in repr(stored_hashes)
    assert owner_capability not in repr(raw_rows)
    assert guest_capability not in repr(raw_rows)

    owner_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=owner_peer_id,
        membership_capability=owner_capability,
    )
    guest_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=guest_peer_id,
        membership_capability=guest_capability,
    )
    forged_owner = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=owner_peer_id,
        membership_capability=guest_capability,
    )
    assert owner_packages["packages"][0]["peer_id"] == guest_peer_id
    assert guest_packages["packages"][0]["peer_id"] == owner_peer_id
    assert forged_owner == {"ok": False, "reason": "membership_capability_invalid"}

    confirmation_tag = base64.b64encode(b"v" * 32).decode("ascii")
    confirmed = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_peer_id,
        recipient_peer_id=guest_peer_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=confirmation_tag,
        sender_account_id=account_id,
        membership_capability=owner_capability,
    )
    fetched_confirmation = public_service.get_key_confirmation(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=guest_peer_id,
        sender_peer_id=owner_peer_id,
        membership_capability=guest_capability,
    )
    forged_confirmation = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_peer_id,
        recipient_peer_id=guest_peer_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=confirmation_tag,
        sender_account_id=account_id,
        membership_capability=guest_capability,
    )
    assert confirmed["ok"] is True
    assert fetched_confirmation["confirmation"]["confirmation_tag"] == confirmation_tag
    assert forged_confirmation == {"ok": False, "reason": "membership_capability_invalid"}
    guest_confirmed = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=guest_peer_id,
        recipient_peer_id=owner_peer_id,
        package_id=guest_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=base64.b64encode(b"w" * 32).decode("ascii"),
        sender_account_id=account_id,
        membership_capability=guest_capability,
    )
    assert guest_confirmed["ok"] is True

    pushed = public_service.push_signal(
        session_id=session["id"],
        sender_id=owner_peer_id,
        recipient_id=guest_peer_id,
        signal_type="offer",
        payload={"sdp": "opaque"},
        security_epoch=2,
        sender_account_id=account_id,
        membership_capability=owner_capability,
    )
    polled = public_service.poll_signals(
        session_id=session["id"],
        user_id=account_id,
        requester_peer_id=guest_peer_id,
        membership_capability=guest_capability,
        security_epoch=2,
    )
    reflected = public_service.push_signal(
        session_id=session["id"],
        sender_id=owner_peer_id,
        recipient_id=owner_peer_id,
        signal_type="offer",
        payload={},
        security_epoch=2,
        sender_account_id=account_id,
        membership_capability=owner_capability,
    )
    assert pushed["ok"] is True
    assert polled["local_peer_id"] == guest_peer_id
    assert polled["signals"][0]["sender_id"] == owner_peer_id
    assert reflected == {"ok": False, "reason": "recipient_not_authorized"}

    monkeypatch.setattr(public_service.cfg, "TURN_SHARED_SECRET", "turn-only-test-secret")
    monkeypatch.setattr(public_service.cfg, "TURN_URLS", ["turn:relay.example:3478"])
    owner_turn = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=owner_peer_id,
        membership_capability=owner_capability,
    )
    guest_turn = public_service.issue_turn_credentials(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=guest_peer_id,
        membership_capability=guest_capability,
    )
    assert owner_turn["credentials"]["local_peer_id"] == owner_peer_id
    assert guest_turn["credentials"]["local_peer_id"] == guest_peer_id
    assert owner_turn["credentials"]["username"] != guest_turn["credentials"]["username"]


def test_v2_runtime_switch_parks_previous_peer_and_fences_stale_signaling(
    public_service,
    monkeypatch,
):
    pair_a = _public_media_pair(
        public_service,
        owner_media_version=0,
        guest_media_version=0,
    )
    session_a = pair_a["session"]
    owner_peer_id = pair_a["owner_peer_id"]

    session_b = public_service.create_session(
        **{
            **pair_a["create_kwargs"],
            "title": "Pair B",
            "membership_capability": "T" * 43,
        }
    )
    guest_b_spki, guest_b_fingerprint = _device_key()
    guest_b_account = _peer_id("runtime-guest-b")
    joined_b = public_service.join_session(
        invite_code=session_b["invite_code"],
        user_id=guest_b_account,
        user_sub="runtime-guest-b",
        device_id="runtime-guest-b-device",
        device_fingerprint=guest_b_fingerprint,
        public_key_spki_b64=guest_b_spki,
        oidc_issuer="https://issuer",
        expected_identity_binding_version=2,
        membership_capability="U" * 43,
    )
    assert session_b["owner_peer_id"] == owner_peer_id
    assert session_b["local_role"] == "owner"
    assert session_b["local_runtime_state"] == "active"
    assert joined_b["session"]["local_role"] == "participant"
    assert joined_b["session"]["local_runtime_state"] == "active"

    catalog_a = next(
        item
        for item in public_service.list_sessions_for_membership_proofs(
            requester_user_id=pair_a["account_id"],
            membership_proofs=[
                {
                    "session_id": session_a["id"],
                    "local_peer_id": owner_peer_id,
                    "membership_capability": pair_a["owner_capability"],
                }
            ],
        )
        if item["id"] == session_a["id"]
    )
    assert catalog_a["local_runtime_state"] == "parked"

    activated_a = public_service.set_membership_runtime(
        session_id=session_a["id"],
        account_id=pair_a["account_id"],
        requested_peer_id=owner_peer_id,
        membership_capability=pair_a["owner_capability"],
        state="active",
    )
    repeated_a = public_service.set_membership_runtime(
        session_id=session_a["id"],
        account_id=pair_a["account_id"],
        requested_peer_id=owner_peer_id,
        membership_capability=pair_a["owner_capability"],
        state="active",
    )
    assert activated_a["changed"] is True
    assert activated_a["parked_session_ids"] == [session_b["id"]]
    assert repeated_a == {
        "ok": True,
        "local_peer_id": owner_peer_id,
        "state": "active",
        "security_epoch": activated_a["security_epoch"],
        "changed": False,
        "parked_session_ids": [],
    }

    packages_a = _confirm_v2_transport(
        public_service,
        session_id=session_a["id"],
        epoch=activated_a["security_epoch"],
        owner_account_id=pair_a["account_id"],
        owner_peer_id=owner_peer_id,
        owner_capability=pair_a["owner_capability"],
        guest_account_id=pair_a["account_id"],
        guest_peer_id=pair_a["guest_peer_id"],
        guest_capability=pair_a["guest_capability"],
    )
    missing_epoch_signal = public_service.push_signal(
        session_id=session_a["id"],
        sender_id=owner_peer_id,
        recipient_id=pair_a["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "missing-generation"},
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    first_signal = public_service.push_signal(
        session_id=session_a["id"],
        sender_id=owner_peer_id,
        recipient_id=pair_a["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "generation-a"},
        security_epoch=activated_a["security_epoch"],
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    assert missing_epoch_signal == {"ok": False, "reason": "signal_epoch_required"}
    assert first_signal["sequence"] == "1"

    activated_b = public_service.set_membership_runtime(
        session_id=session_b["id"],
        account_id=pair_a["account_id"],
        requested_peer_id=owner_peer_id,
        membership_capability="T" * 43,
        state="active",
    )
    assert activated_b["changed"] is True
    assert activated_b["parked_session_ids"] == [session_a["id"]]

    parked_packages = public_service.get_key_packages(
        session_id=session_a["id"],
        requester_user_id=pair_a["account_id"],
        requester_peer_id=pair_a["guest_peer_id"],
        membership_capability=pair_a["guest_capability"],
    )
    denied_confirmation = public_service.put_key_confirmation(
        session_id=session_a["id"],
        sender_peer_id=owner_peer_id,
        recipient_peer_id=pair_a["guest_peer_id"],
        package_id=packages_a["owner"]["packages"][0]["package_id"],
        epoch=activated_a["security_epoch"],
        confirmation_tag=base64.b64encode(b"z" * 32).decode("ascii"),
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    monkeypatch.setattr(public_service.cfg, "TURN_SHARED_SECRET", "turn-only-test-secret")
    monkeypatch.setattr(public_service.cfg, "TURN_URLS", ["turn:relay.example:3478"])
    denied_turn = public_service.issue_turn_credentials(
        session_id=session_a["id"],
        requester_user_id=pair_a["account_id"],
        requester_peer_id=pair_a["guest_peer_id"],
        membership_capability=pair_a["guest_capability"],
    )
    stale_signal = public_service.push_signal(
        session_id=session_a["id"],
        sender_id=owner_peer_id,
        recipient_id=pair_a["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "stale-generation"},
        security_epoch=activated_a["security_epoch"],
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    parked_signal = public_service.push_signal(
        session_id=session_a["id"],
        sender_id=owner_peer_id,
        recipient_id=pair_a["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "parked-generation"},
        security_epoch=parked_packages["epoch"],
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    assert parked_packages["packages"] == []
    assert parked_packages["transport_ready"] is False
    assert parked_packages["local_runtime_state"] == "active"
    assert parked_packages["peer_runtime_state"] == "parked"
    assert denied_confirmation == {"ok": False, "reason": "pair_runtime_not_ready"}
    assert denied_turn == {"ok": False, "reason": "pair_runtime_not_ready"}
    assert stale_signal == {"ok": False, "reason": "epoch_mismatch"}
    assert parked_signal == {"ok": False, "reason": "pair_runtime_not_ready"}

    resumed_a = public_service.set_membership_runtime(
        session_id=session_a["id"],
        account_id=pair_a["account_id"],
        requested_peer_id=owner_peer_id,
        membership_capability=pair_a["owner_capability"],
        state="active",
    )
    assert resumed_a["security_epoch"] > parked_packages["epoch"]
    _confirm_v2_transport(
        public_service,
        session_id=session_a["id"],
        epoch=resumed_a["security_epoch"],
        owner_account_id=pair_a["account_id"],
        owner_peer_id=owner_peer_id,
        owner_capability=pair_a["owner_capability"],
        guest_account_id=pair_a["account_id"],
        guest_peer_id=pair_a["guest_peer_id"],
        guest_capability=pair_a["guest_capability"],
    )
    missing_epoch_poll = public_service.poll_signals(
        session_id=session_a["id"],
        user_id=pair_a["account_id"],
        requester_peer_id=pair_a["guest_peer_id"],
        membership_capability=pair_a["guest_capability"],
    )
    fresh_signal = public_service.push_signal(
        session_id=session_a["id"],
        sender_id=owner_peer_id,
        recipient_id=pair_a["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "fresh-generation"},
        security_epoch=resumed_a["security_epoch"],
        sender_account_id=pair_a["account_id"],
        membership_capability=pair_a["owner_capability"],
    )
    fresh_poll = public_service.poll_signals(
        session_id=session_a["id"],
        user_id=pair_a["account_id"],
        requester_peer_id=pair_a["guest_peer_id"],
        membership_capability=pair_a["guest_capability"],
        security_epoch=resumed_a["security_epoch"],
    )
    stale_poll = public_service.poll_signals(
        session_id=session_a["id"],
        user_id=pair_a["account_id"],
        requester_peer_id=pair_a["guest_peer_id"],
        membership_capability=pair_a["guest_capability"],
        security_epoch=activated_a["security_epoch"],
    )
    assert missing_epoch_poll == {"ok": False, "reason": "signal_epoch_required"}
    assert fresh_signal["sequence"] == "1"
    assert fresh_poll["security_epoch"] == resumed_a["security_epoch"]
    assert [signal["payload"]["sdp"] for signal in fresh_poll["signals"]] == ["fresh-generation"]
    assert stale_poll == {"ok": False, "reason": "epoch_mismatch"}


def test_v2_create_recovery_reactivates_exact_target_and_parks_other_session(public_service):
    pair_a = _public_media_pair(
        public_service,
        owner_media_version=0,
        guest_media_version=0,
    )
    session_a = pair_a["session"]
    owner_peer_id = pair_a["owner_peer_id"]
    session_b = public_service.create_session(
        **{
            **pair_a["create_kwargs"],
            "title": "Committed after lost response",
            "membership_capability": "T" * 43,
        }
    )
    parked_a = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_a["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": pair_a["owner_capability"],
            }
        ],
    )[0]
    active_b = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_b["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": "T" * 43,
            }
        ],
    )[0]
    assert parked_a["local_runtime_state"] == "parked"
    assert active_b["local_runtime_state"] == "active"

    # The first create response was lost. An exact immutable retry with the
    # retained pending capability is the recovery action selected by the user.
    recovered_a = public_service.create_session(**pair_a["create_kwargs"])
    repeated_a = public_service.create_session(**pair_a["create_kwargs"])
    recovered_catalog_a = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_a["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": pair_a["owner_capability"],
            }
        ],
    )[0]
    parked_catalog_b = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_b["id"],
                "local_peer_id": owner_peer_id,
                "membership_capability": "T" * 43,
            }
        ],
    )[0]

    assert recovered_a["_idempotent"] is True
    assert recovered_a["local_runtime_state"] == "active"
    assert recovered_a["security_epoch"] > parked_a["security_epoch"]
    assert repeated_a["security_epoch"] == recovered_a["security_epoch"]
    assert recovered_catalog_a["local_runtime_state"] == "active"
    assert recovered_catalog_a["security_epoch"] == recovered_a["security_epoch"]
    assert parked_catalog_b["local_runtime_state"] == "parked"
    assert parked_catalog_b["security_epoch"] > active_b["security_epoch"]


def test_v2_join_recovery_reactivates_exact_target_and_parks_other_session(public_service):
    pair_a = _public_media_pair(
        public_service,
        owner_media_version=0,
        guest_media_version=0,
    )
    session_a = pair_a["session"]
    session_b = _create_session(
        public_service,
        subject="join-recovery-owner-b",
        title="Other participant session",
        identity_binding_version=2,
        membership_capability="V" * 43,
    )
    guest_b_capability = "W" * 43
    joined_b = public_service.join_session(
        **{
            **pair_a["join_kwargs"],
            "invite_code": session_b["invite_code"],
            "membership_capability": guest_b_capability,
        }
    )
    parked_a = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_a["id"],
                "local_peer_id": pair_a["guest_peer_id"],
                "membership_capability": pair_a["guest_capability"],
            }
        ],
    )[0]
    active_b = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_b["id"],
                "local_peer_id": pair_a["guest_peer_id"],
                "membership_capability": guest_b_capability,
            }
        ],
    )[0]
    assert joined_b["session"]["local_runtime_state"] == "active"
    assert parked_a["local_runtime_state"] == "parked"
    assert active_b["local_runtime_state"] == "active"

    # The original A join committed but its response was lost. The exact
    # capability-bound retry must recover A instead of leaving it unbindable.
    recovered_a = public_service.join_session(**pair_a["join_kwargs"])
    repeated_a = public_service.join_session(**pair_a["join_kwargs"])
    recovered_catalog_a = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_a["id"],
                "local_peer_id": pair_a["guest_peer_id"],
                "membership_capability": pair_a["guest_capability"],
            }
        ],
    )[0]
    parked_catalog_b = public_service.list_sessions_for_membership_proofs(
        requester_user_id=pair_a["account_id"],
        membership_proofs=[
            {
                "session_id": session_b["id"],
                "local_peer_id": pair_a["guest_peer_id"],
                "membership_capability": guest_b_capability,
            }
        ],
    )[0]

    assert recovered_a["idempotent"] is True
    assert recovered_a["session"]["local_runtime_state"] == "active"
    assert recovered_a["session"]["security_epoch"] > parked_a["security_epoch"]
    assert repeated_a["session"]["security_epoch"] == recovered_a["session"]["security_epoch"]
    assert recovered_catalog_a["local_runtime_state"] == "active"
    assert recovered_catalog_a["security_epoch"] == recovered_a["session"]["security_epoch"]
    assert parked_catalog_b["local_runtime_state"] == "parked"
    assert parked_catalog_b["security_epoch"] > active_b["security_epoch"]


def test_v2_guest_leave_is_exact_idempotent_and_rekeys_cleanly(public_service):
    pair = _public_media_pair(
        public_service,
        owner_media_version=2,
        guest_media_version=2,
    )
    session_id = pair["session"]["id"]
    initial_epoch = pair["joined"]["session"]["security_epoch"]
    _confirm_v2_transport(
        public_service,
        session_id=session_id,
        epoch=initial_epoch,
        owner_account_id=pair["account_id"],
        owner_peer_id=pair["owner_peer_id"],
        owner_capability=pair["owner_capability"],
        guest_account_id=pair["account_id"],
        guest_peer_id=pair["guest_peer_id"],
        guest_capability=pair["guest_capability"],
    )
    signaled = public_service.push_signal(
        session_id=session_id,
        sender_id=pair["owner_peer_id"],
        recipient_id=pair["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "retired-pair-offer"},
        security_epoch=initial_epoch,
        sender_account_id=pair["account_id"],
        membership_capability=pair["owner_capability"],
    )
    owner_attempt = public_service.leave_session(
        session_id=session_id,
        actor_user_id=pair["account_id"],
        actor_peer_id=pair["owner_peer_id"],
        membership_capability=pair["owner_capability"],
    )
    forged_attempt = public_service.leave_session(
        session_id=session_id,
        actor_user_id=pair["account_id"],
        actor_peer_id=pair["guest_peer_id"],
        membership_capability="Z" * 43,
    )
    left = public_service.leave_session(
        session_id=session_id,
        actor_user_id=pair["account_id"],
        actor_peer_id=pair["guest_peer_id"],
        membership_capability=pair["guest_capability"],
    )
    repeated = public_service.leave_session(
        session_id=session_id,
        actor_user_id=pair["account_id"],
        actor_peer_id=pair["guest_peer_id"],
        membership_capability=pair["guest_capability"],
    )

    with public_service._db() as conn:
        stored_epoch = conn.execute(
            "SELECT security_epoch FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()[0]
        participant_rows = conn.execute(
            "SELECT revoked_at FROM participants WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        signal_count = conn.execute(
            "SELECT COUNT(1) FROM signals WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        confirmation_count = conn.execute(
            "SELECT COUNT(1) FROM key_confirmations WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    owner_packages = public_service.get_key_packages(
        session_id=session_id,
        requester_user_id=pair["account_id"],
        requester_peer_id=pair["owner_peer_id"],
        membership_capability=pair["owner_capability"],
    )

    assert signaled["ok"] is True
    assert owner_attempt == {"ok": False, "reason": "owner_must_end_session"}
    assert forged_attempt == {"ok": False, "reason": "membership_capability_invalid"}
    assert left == {
        "ok": True,
        "local_peer_id": pair["guest_peer_id"],
        "idempotent": False,
    }
    assert repeated == {
        "ok": True,
        "local_peer_id": pair["guest_peer_id"],
        "idempotent": True,
    }
    assert stored_epoch == initial_epoch + 1
    assert len(participant_rows) == 1 and participant_rows[0][0] is not None
    assert signal_count == 0
    assert confirmation_count == 0
    assert owner_packages["epoch"] == initial_epoch + 1
    assert owner_packages["packages"] == []


def test_owner_end_removes_retained_pair_security_artifacts(public_service):
    pair = _public_media_pair(
        public_service,
        owner_media_version=2,
        guest_media_version=2,
    )
    session_id = pair["session"]["id"]
    epoch = pair["joined"]["session"]["security_epoch"]
    _confirm_v2_transport(
        public_service,
        session_id=session_id,
        epoch=epoch,
        owner_account_id=pair["account_id"],
        owner_peer_id=pair["owner_peer_id"],
        owner_capability=pair["owner_capability"],
        guest_account_id=pair["account_id"],
        guest_peer_id=pair["guest_peer_id"],
        guest_capability=pair["guest_capability"],
    )
    public_service.push_signal(
        session_id=session_id,
        sender_id=pair["owner_peer_id"],
        recipient_id=pair["guest_peer_id"],
        signal_type="offer",
        payload={"sdp": "ending-pair-offer"},
        security_epoch=epoch,
        sender_account_id=pair["account_id"],
        membership_capability=pair["owner_capability"],
    )

    ended = public_service.revoke_session(
        session_id=session_id,
        actor_user_id=pair["account_id"],
        actor_peer_id=pair["owner_peer_id"],
        membership_capability=pair["owner_capability"],
    )

    with public_service._db() as conn:
        signal_count = conn.execute(
            "SELECT COUNT(1) FROM signals WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        confirmation_count = conn.execute(
            "SELECT COUNT(1) FROM key_confirmations WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    assert ended["ok"] is True
    assert signal_count == 0
    assert confirmation_count == 0


def test_parallel_guest_leave_revokes_once_and_returns_idempotently(public_service):
    pair = _public_media_pair(
        public_service,
        owner_media_version=2,
        guest_media_version=2,
    )
    session_id = pair["session"]["id"]
    initial_epoch = pair["joined"]["session"]["security_epoch"]
    ready = threading.Barrier(3)
    results: list[dict] = []
    failures: list[BaseException] = []

    def leave() -> None:
        try:
            ready.wait(timeout=5)
            results.append(public_service.leave_session(
                session_id=session_id,
                actor_user_id=pair["account_id"],
                actor_peer_id=pair["guest_peer_id"],
                membership_capability=pair["guest_capability"],
            ))
        except BaseException as exc:  # pragma: no cover - asserted by parent thread
            failures.append(exc)

    first = threading.Thread(target=leave)
    second = threading.Thread(target=leave)
    first.start()
    second.start()
    ready.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)

    with public_service._db() as conn:
        stored_epoch = conn.execute(
            "SELECT security_epoch FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()[0]
    assert first.is_alive() is False
    assert second.is_alive() is False
    assert failures == []
    assert sorted(result["idempotent"] for result in results) == [False, True]
    assert {result["local_peer_id"] for result in results} == {pair["guest_peer_id"]}
    assert stored_epoch == initial_epoch + 1


def test_v2_create_and_join_retries_require_the_original_capability(public_service):
    account_id = _peer_id("shared-sub")
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    owner_capability = "C" * 43
    guest_capability = "D" * 43
    create_kwargs = {
        "owner_user_id": account_id,
        "owner_user_sub": "shared-sub",
        "owner_device_id": "owner-device",
        "owner_device_fingerprint": owner_fp,
        "owner_public_key_spki_b64": owner_spki,
        "oidc_issuer": "https://issuer",
        "title": "Recoverable pair",
        "identity_binding_version": 2,
        "membership_capability": owner_capability,
    }
    created = public_service.create_session(**create_kwargs)
    recovered = public_service.create_session(**create_kwargs)
    assert recovered["id"] == created["id"]
    assert recovered["_idempotent"] is True
    with pytest.raises(ValueError, match="membership_capability_conflict"):
        public_service.create_session(**{**create_kwargs, "title": "Changed request"})

    join_kwargs = {
        "invite_code": created["invite_code"],
        "user_id": account_id,
        "user_sub": "shared-sub",
        "device_id": "guest-device",
        "device_fingerprint": guest_fp,
        "public_key_spki_b64": guest_spki,
        "oidc_issuer": "https://issuer",
        "expected_identity_binding_version": 2,
        "membership_capability": guest_capability,
    }
    joined = public_service.join_session(**join_kwargs)
    recovered_join = public_service.join_session(**join_kwargs)
    missing_capability = public_service.join_session(**{**join_kwargs, "membership_capability": ""})
    wrong_capability = public_service.join_session(**{**join_kwargs, "membership_capability": "E" * 43})
    changed_device_label = public_service.join_session(**{**join_kwargs, "device_id": "changed-device-label"})
    assert joined["ok"] is True
    assert recovered_join["idempotent"] is True
    assert missing_capability == {"ok": False, "reason": "membership_capability_required"}
    assert wrong_capability == {"ok": False, "reason": "membership_capability_invalid"}
    assert changed_device_label == {"ok": False, "reason": "device_identity_conflict"}

    revoked = public_service.revoke_session(
        session_id=created["id"],
        actor_user_id=account_id,
        actor_peer_id=created["owner_peer_id"],
        membership_capability=owner_capability,
    )
    assert revoked["ok"] is True
    assert (
        public_service.is_owner_create_recovery(
            account_id=account_id,
            device_fingerprint=owner_fp,
            membership_capability=owner_capability,
        )
        is False
    )
    with pytest.raises(ValueError, match="membership_capability_retired"):
        public_service.create_session(**create_kwargs)


def test_public_media_v1_requires_the_exact_closed_capability_advertisement(public_service):
    base_kwargs = {
        "subject": "media-owner-sub",
        "identity_binding_version": 2,
        "membership_capability": "M" * 43,
    }
    exact = _public_media_capabilities_v1()

    session = _create_session(
        public_service,
        **base_kwargs,
        public_media_e2ee_version=1,
        public_media_capabilities=exact,
    )

    assert session["public_media_e2ee_version"] == 1
    assert session["public_media_capabilities"] == exact
    with pytest.raises(ValueError, match="public_media_capabilities_without_version"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "N" * 43},
            public_media_capabilities=exact,
        )
    with pytest.raises(ValueError, match="public_media_capabilities_invalid"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "P" * 43},
            public_media_e2ee_version=1,
            public_media_capabilities={**exact, "grants": list(reversed(exact["grants"]))},
        )
    with pytest.raises(ValueError, match="public_media_capabilities_invalid"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "Q" * 43},
            public_media_e2ee_version=1,
            public_media_capabilities={**exact, "future": True},
        )
    with pytest.raises(ValueError, match="public_media_capabilities_invalid"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "T" * 43},
            public_media_e2ee_version=1,
            public_media_capabilities={**exact, "version": True},
        )
    with pytest.raises(ValueError, match="public_media_capabilities_invalid"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "V" * 43},
            public_media_e2ee_version=1,
            public_media_capabilities={
                **exact,
                "grants": ["microphone-opus", "camera-vp8", "camera-vp8"],
            },
        )
    with pytest.raises(ValueError, match="public_media_e2ee_version_unsupported"):
        _create_session(
            public_service,
            **{**base_kwargs, "membership_capability": "U" * 43},
            public_media_e2ee_version=True,
            public_media_capabilities=exact,
        )
    with pytest.raises(ValueError, match="public_media_identity_binding_v2_required"):
        _create_session(
            public_service,
            subject="media-v1-owner-sub",
            public_media_e2ee_version=1,
            public_media_capabilities=exact,
        )


def test_public_media_v2_requires_the_exact_frame_format_capability(public_service):
    base_kwargs = {
        "subject": "media-v2-owner-sub",
        "identity_binding_version": 2,
        "membership_capability": "W" * 43,
    }
    exact = _public_media_capabilities_v2()

    session = _create_session(
        public_service,
        **base_kwargs,
        public_media_e2ee_version=2,
        public_media_capabilities=exact,
    )

    assert session["public_media_e2ee_version"] == 2
    assert session["public_media_capabilities"] == exact
    invalid_capabilities = (
        {key: value for key, value in exact.items() if key != "frame_format"},
        {**exact, "frame_format": "ananta.public-pair.media-frame.v1"},
        {**exact, "frame_format": True},
        {**exact, "version": 1},
        {**exact, "future": True},
        _public_media_capabilities_v1(),
    )
    for capabilities in invalid_capabilities:
        with pytest.raises(ValueError, match="public_media_capabilities_invalid"):
            _create_session(
                public_service,
                **base_kwargs,
                public_media_e2ee_version=2,
                public_media_capabilities=capabilities,
            )

    with pytest.raises(ValueError, match="public_media_e2ee_version_unsupported"):
        _create_session(
            public_service,
            **base_kwargs,
            public_media_e2ee_version=3,
            public_media_capabilities=exact,
        )


def test_public_media_v1_contract_is_stable_exact_and_ed25519_signed(public_service):
    pair = _public_media_pair(public_service, owner_media_version=1, guest_media_version=1)
    owner_response = pair["owner_packages"]
    guest_response = pair["guest_packages"]
    contract = owner_response["public_media_security_contract_v1"]

    assert contract == guest_response["public_media_security_contract_v1"]
    assert owner_response["public_media_security_contract_v2"] is None
    assert guest_response["public_media_security_contract_v2"] is None
    repeated = public_service.get_key_packages(
        session_id=pair["session"]["id"],
        requester_user_id=pair["account_id"],
        requester_peer_id=pair["owner_peer_id"],
        membership_capability=pair["owner_capability"],
    )
    assert repeated["public_media_security_contract_v1"] == contract
    assert set(contract) == {
        "domain",
        "version",
        "session_id",
        "epoch",
        "identity_binding_version",
        "base_security_contract_digest",
        "memberships",
        "grants",
        "slots",
        "transform",
        "algorithms",
        "expires_at_ms",
        "authority_key_id",
        "digest",
        "signature_algorithm",
        "signature_b64",
    }
    assert contract["domain"] == "ananta.public-pair.media-security-contract.v1"
    assert contract["version"] == 1
    assert contract["session_id"] == pair["session"]["id"]
    assert contract["epoch"] == 2
    assert contract["identity_binding_version"] == 2
    assert contract["base_security_contract_digest"] == owner_response["security_contract_digest"]
    assert [member["peer_id"] for member in contract["memberships"]] == [
        pair["owner_peer_id"],
        pair["guest_peer_id"],
    ]
    assert all(
        set(member)
        == {
            "membership_id",
            "membership_version",
            "peer_id",
            "device_key_fingerprint",
            "public_media_e2ee_version",
        }
        for member in contract["memberships"]
    )
    assert all(member["public_media_e2ee_version"] == 1 for member in contract["memberships"])
    assert contract["grants"] == ["microphone-opus", "camera-vp8", "screen-vp8"]
    assert contract["slots"] == [
        {"slot": "microphone-opus", "kind": "audio", "codec": "opus"},
        {"slot": "camera-vp8", "kind": "video", "codec": "vp8"},
        {"slot": "screen-vp8", "kind": "video", "codec": "vp8"},
    ]
    assert contract["transform"] == "RTCRtpScriptTransform"
    assert contract["algorithms"] == {"aead": "AES-256-GCM", "kdf": "HKDF-SHA-256"}
    assert contract["expires_at_ms"] == int(pair["session"]["expires_at"] * 1000)
    assert contract["authority_key_id"] == owner_response["hub_key_id"]
    _verify_public_media_contract(contract, owner_response["hub_public_key_b64"])

    from cryptography.exceptions import InvalidSignature

    tampered_contracts = []
    for path, value in (
        (("base_security_contract_digest",), "0" * 64),
        (("epoch",), contract["epoch"] + 1),
        (("memberships", 0, "peer_id"), pair["guest_peer_id"]),
        (("memberships", 1, "device_key_fingerprint"), pair["owner_fingerprint"]),
        (("slots", 0, "codec"), "pcmu"),
    ):
        tampered = json.loads(json.dumps(contract))
        cursor = tampered
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        tampered_contracts.append(tampered)

    for tampered in tampered_contracts:
        with pytest.raises(InvalidSignature):
            _verify_public_media_contract(tampered, owner_response["hub_public_key_b64"], check_digest=False)
        with pytest.raises(AssertionError):
            _verify_public_media_contract(tampered, owner_response["hub_public_key_b64"])


def test_public_media_v2_contract_binds_exact_frame_format_and_is_ed25519_signed(public_service):
    pair = _public_media_pair(public_service, owner_media_version=2, guest_media_version=2)
    owner_response = pair["owner_packages"]
    guest_response = pair["guest_packages"]
    contract = owner_response["public_media_security_contract_v2"]

    assert contract == guest_response["public_media_security_contract_v2"]
    assert owner_response["public_media_security_contract_v1"] is None
    assert guest_response["public_media_security_contract_v1"] is None
    repeated = public_service.get_key_packages(
        session_id=pair["session"]["id"],
        requester_user_id=pair["account_id"],
        requester_peer_id=pair["owner_peer_id"],
        membership_capability=pair["owner_capability"],
    )
    assert repeated["public_media_security_contract_v2"] == contract
    assert set(contract) == {
        "domain",
        "version",
        "session_id",
        "epoch",
        "identity_binding_version",
        "base_security_contract_digest",
        "memberships",
        "grants",
        "slots",
        "transform",
        "algorithms",
        "expires_at_ms",
        "authority_key_id",
        "frame_format",
        "digest",
        "signature_algorithm",
        "signature_b64",
    }
    assert contract["domain"] == "ananta.public-pair.media-security-contract.v2"
    assert contract["version"] == 2
    assert contract["frame_format"] == "ananta.public-pair.media-frame.v2"
    assert contract["identity_binding_version"] == 2
    assert contract["base_security_contract_digest"] == owner_response["security_contract_digest"]
    assert all(member["membership_version"] == 1 for member in contract["memberships"])
    assert all(member["public_media_e2ee_version"] == 2 for member in contract["memberships"])
    assert contract["grants"] == ["microphone-opus", "camera-vp8", "screen-vp8"]
    assert contract["transform"] == "RTCRtpScriptTransform"
    _verify_public_media_contract(contract, owner_response["hub_public_key_b64"])

    from cryptography.exceptions import InvalidSignature

    for path, value in (
        (("version",), 1),
        (("frame_format",), "ananta.public-pair.media-frame.v1"),
        (("memberships", 0, "public_media_e2ee_version"), 1),
    ):
        tampered = json.loads(json.dumps(contract))
        cursor = tampered
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        with pytest.raises(InvalidSignature):
            _verify_public_media_contract(tampered, owner_response["hub_public_key_b64"], check_digest=False)
        with pytest.raises(AssertionError):
            _verify_public_media_contract(tampered, owner_response["hub_public_key_b64"])


@pytest.mark.parametrize(
    ("owner_version", "guest_version"),
    [(1, 0), (0, 1), (0, 0), (2, 0), (0, 2), (1, 2), (2, 1)],
)
def test_public_media_contract_is_null_unless_both_members_advertise_the_same_version(
    public_service,
    owner_version,
    guest_version,
):
    pair = _public_media_pair(
        public_service,
        owner_media_version=owner_version,
        guest_media_version=guest_version,
    )

    assert pair["owner_packages"]["public_media_security_contract_v1"] is None
    assert pair["guest_packages"]["public_media_security_contract_v1"] is None
    assert pair["owner_packages"]["public_media_security_contract_v2"] is None
    assert pair["guest_packages"]["public_media_security_contract_v2"] is None
    assert pair["owner_packages"]["security_contract"] is not None
    assert pair["guest_packages"]["security_contract"] is not None


@pytest.mark.parametrize("media_version", [1, 2])
def test_public_media_version_is_bound_to_create_and_join_idempotency(public_service, media_version):
    pair = _public_media_pair(
        public_service,
        owner_media_version=media_version,
        guest_media_version=media_version,
    )
    exact = _public_media_capabilities_for_version(media_version)

    recovered_owner = public_service.create_session(**pair["create_kwargs"])
    recovered_guest = public_service.join_session(**pair["join_kwargs"])
    assert recovered_owner["_idempotent"] is True
    assert recovered_guest["idempotent"] is True
    assert public_service.is_owner_create_recovery(
        account_id=pair["account_id"],
        device_fingerprint=pair["owner_fingerprint"],
        membership_capability=pair["owner_capability"],
        public_media_e2ee_version=media_version,
    )
    assert public_service.is_join_recovery(
        invite_code=pair["session"]["invite_code"],
        account_id=pair["account_id"],
        device_fingerprint=pair["guest_fingerprint"],
        membership_capability=pair["guest_capability"],
        public_media_e2ee_version=media_version,
    )
    with public_service._db() as conn:
        persisted_owner_version = conn.execute(
            "SELECT public_media_e2ee_version FROM sessions WHERE id = ?",
            (pair["session"]["id"],),
        ).fetchone()[0]
        persisted_guest_version = conn.execute(
            "SELECT public_media_e2ee_version FROM participants WHERE session_id = ?",
            (pair["session"]["id"],),
        ).fetchone()[0]
    assert (persisted_owner_version, persisted_guest_version) == (media_version, media_version)
    with pytest.raises(ValueError, match="membership_capability_conflict"):
        public_service.create_session(
            **{
                **pair["create_kwargs"],
                "public_media_e2ee_version": 0,
                "public_media_capabilities": None,
            }
        )
    conflicting_join = public_service.join_session(
        **{
            **pair["join_kwargs"],
            "public_media_e2ee_version": 0,
            "public_media_capabilities": None,
        }
    )
    assert conflicting_join == {"ok": False, "reason": "public_media_capability_conflict"}
    assert (
        public_service.is_owner_create_recovery(
            account_id=pair["account_id"],
            device_fingerprint=pair["owner_fingerprint"],
            membership_capability=pair["owner_capability"],
            public_media_e2ee_version=0,
        )
        is False
    )
    assert (
        public_service.is_join_recovery(
            invite_code=pair["session"]["invite_code"],
            account_id=pair["account_id"],
            device_fingerprint=pair["guest_fingerprint"],
            membership_capability=pair["guest_capability"],
            public_media_e2ee_version=0,
        )
        is False
    )
    assert exact == pair["create_kwargs"]["public_media_capabilities"]


def test_v2_rejects_cloned_device_key_and_version_downgrade(public_service):
    account_id = _peer_id("shared-sub")
    owner_spki, owner_fp = _device_key()
    session = public_service.create_session(
        owner_user_id=account_id,
        owner_user_sub="shared-sub",
        owner_device_id="owner-device",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        requested_expires_at=public_service._now() + 600,
        identity_binding_version=2,
        membership_capability="F" * 43,
    )
    cloned_key = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=account_id,
        user_sub="shared-sub",
        device_id="different-label",
        device_fingerprint=owner_fp,
        public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        expected_identity_binding_version=2,
        membership_capability="G" * 43,
    )
    downgraded = public_service.join_session(
        invite_code=session["invite_code"],
        user_id=account_id,
        user_sub="shared-sub",
        device_id="different-label",
        device_fingerprint=owner_fp,
        public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        expected_identity_binding_version=1,
    )
    assert cloned_key == {"ok": False, "reason": "device_key_must_be_distinct"}
    assert downgraded == {"ok": False, "reason": "identity_binding_version_mismatch"}


def test_v2_join_requires_explicit_identity_binding_negotiation(public_service):
    account_id = _peer_id("shared-sub")
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    session = public_service.create_session(
        owner_user_id=account_id,
        owner_user_sub="shared-sub",
        owner_device_id="owner-device",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
        requested_expires_at=public_service._now() + 600,
        identity_binding_version=2,
        membership_capability="H" * 43,
    )
    join_kwargs = {
        "invite_code": session["invite_code"],
        "user_id": account_id,
        "user_sub": "shared-sub",
        "device_id": "guest-device",
        "device_fingerprint": guest_fp,
        "public_key_spki_b64": guest_spki,
        "oidc_issuer": "https://issuer",
        "membership_capability": "I" * 43,
    }

    unnegotiated = public_service.join_session(**join_kwargs)
    negotiated = public_service.join_session(
        **join_kwargs,
        expected_identity_binding_version=2,
    )

    assert unnegotiated == {"ok": False, "reason": "identity_binding_version_mismatch"}
    assert negotiated["ok"] is True
    assert negotiated["session"]["identity_binding_version"] == 2


def test_v1_join_without_identity_binding_version_remains_compatible(public_service):
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
    assert joined["session"]["identity_binding_version"] == 1


def test_confirmation_refresh_renews_only_the_authenticated_immutable_binding(
    public_service,
    monkeypatch,
):
    session, owner_packages, guest_packages = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    tag = base64.b64encode(b"a" * 32).decode("ascii")
    now = public_service._now()
    monkeypatch.setattr(public_service, "_now", lambda: now)

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
    renewal_now = now + 120
    monkeypatch.setattr(public_service, "_now", lambda: renewal_now)
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
    assert idempotent["created_at_ms"] == int(renewal_now * 1000)
    assert idempotent["expires_at_ms"] == int((renewal_now + 300) * 1000)
    assert idempotent["expires_at_ms"] > stored["expires_at_ms"]
    assert conflict == {"ok": False, "reason": "key_confirmation_conflict"}
    fetched = public_service.get_key_confirmation(
        session_id=session["id"],
        requester_user_id=guest_id,
        sender_peer_id=owner_id,
    )
    assert fetched["confirmation"]["created_at_ms"] == idempotent["created_at_ms"]
    assert fetched["confirmation"]["expires_at_ms"] == idempotent["expires_at_ms"]
    assert fetched["confirmation"]["confirmation_tag"] == tag


def test_confirmation_refresh_prevents_the_bilateral_expiry_race(public_service, monkeypatch):
    session, owner_packages, guest_packages = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    now = public_service._now()
    monkeypatch.setattr(public_service, "_now", lambda: now)

    directions = (
        (owner_id, guest_id, owner_packages["packages"][0]["package_id"], b"o" * 32),
        (guest_id, owner_id, guest_packages["packages"][0]["package_id"], b"g" * 32),
    )
    for sender_id, recipient_id, package_id, raw_tag in directions:
        stored = public_service.put_key_confirmation(
            session_id=session["id"],
            sender_peer_id=sender_id,
            recipient_peer_id=recipient_id,
            package_id=package_id,
            epoch=2,
            confirmation_tag=base64.b64encode(raw_tag).decode("ascii"),
        )
        assert stored["ok"] is True

    # Each browser refreshes its own direction before the original five-minute
    # lease expires.  When one browser checks just beyond that old boundary,
    # the opposite direction must still be present instead of transiently null.
    for refresh_offset in (120, 240):
        refresh_now = now + refresh_offset
        monkeypatch.setattr(public_service, "_now", lambda: refresh_now)
        for sender_id, recipient_id, package_id, raw_tag in directions:
            renewed = public_service.put_key_confirmation(
                session_id=session["id"],
                sender_peer_id=sender_id,
                recipient_peer_id=recipient_id,
                package_id=package_id,
                epoch=2,
                confirmation_tag=base64.b64encode(raw_tag).decode("ascii"),
            )
            assert renewed["idempotent"] is True
            assert renewed["expires_at_ms"] == int((refresh_now + 300) * 1000)

    # At the next refresh, peer A renews first and immediately reads peer B,
    # matching the live ordering that used to return confirmation:null.
    refresh_now = now + 360
    monkeypatch.setattr(public_service, "_now", lambda: refresh_now)
    sender_id, recipient_id, package_id, raw_tag = directions[0]
    renewed = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=sender_id,
        recipient_peer_id=recipient_id,
        package_id=package_id,
        epoch=2,
        confirmation_tag=base64.b64encode(raw_tag).decode("ascii"),
    )
    assert renewed["expires_at_ms"] == int((refresh_now + 300) * 1000)
    fetched = public_service.get_key_confirmation(
        session_id=session["id"],
        requester_user_id=owner_id,
        sender_peer_id=guest_id,
    )
    assert fetched["ok"] is True
    assert fetched["confirmation"] is not None
    assert fetched["confirmation"]["expires_at_ms"] == int((now + 540) * 1000)


def test_confirmation_refresh_never_exceeds_session_expiry(public_service, monkeypatch):
    session, owner_packages, _ = _joined_session(public_service)
    owner_id = _peer_id("owner-sub")
    guest_id = _peer_id("guest-sub")
    tag = base64.b64encode(b"s" * 32).decode("ascii")
    session_expires_at = float(session["expires_at"])
    initial_now = session_expires_at - 120
    monkeypatch.setattr(public_service, "_now", lambda: initial_now)
    stored = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    assert stored["expires_at_ms"] == int(session_expires_at * 1000)

    renewal_now = initial_now + 60
    monkeypatch.setattr(public_service, "_now", lambda: renewal_now)
    renewed = public_service.put_key_confirmation(
        session_id=session["id"],
        sender_peer_id=owner_id,
        recipient_peer_id=guest_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=2,
        confirmation_tag=tag,
    )
    assert renewed["idempotent"] is True
    assert renewed["created_at_ms"] == int(renewal_now * 1000)
    assert renewed["expires_at_ms"] == int(session_expires_at * 1000)


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
        participant_info = {row[1]: row for row in conn.execute("PRAGMA table_info(participants)")}
        session_media_default = conn.execute(
            "SELECT public_media_e2ee_version FROM sessions WHERE id = 'legacy-session'"
        ).fetchone()[0]
        migrated_signal_rows = conn.execute(
            "SELECT sequence FROM signals WHERE id IN ('signal-a', 'signal-b') ORDER BY sent_at, rowid"
        ).fetchall()
        rollback_signal_rows = conn.execute(
            "SELECT sequence FROM signals WHERE id LIKE 'rollback-signal-%' ORDER BY id"
        ).fetchall()
        signal_indexes = {row[1] for row in conn.execute("PRAGMA index_list(signals)")}
        confirmation_expiry = conn.execute("SELECT expires_at FROM key_confirmations").fetchone()[0]

    with service._db() as conn:
        signal_epochs_before_restart = conn.execute(
            "SELECT id, security_epoch FROM signals ORDER BY id"
        ).fetchall()
        conn.execute("UPDATE sessions SET security_epoch = 9 WHERE id = 'legacy-session'")
        service._migrate_database(conn)
        signal_epochs_after_restart = conn.execute(
            "SELECT id, security_epoch FROM signals ORDER BY id"
        ).fetchall()

    assert "identity_binding_version" in session_columns
    assert "public_media_e2ee_version" in session_columns
    assert "public_media_e2ee_version" in participant_info
    assert session_media_default == 0
    assert participant_info["public_media_e2ee_version"][4] == "0"
    assert migrated_signal_rows == [(1,), (2,)]
    assert rollback_signal_rows == [(0,), (0,)]
    assert [tuple(row) for row in signal_epochs_before_restart] == [
        ("rollback-signal-1", 0),
        ("rollback-signal-2", 0),
        ("signal-a", 1),
        ("signal-b", 1),
    ]
    assert [tuple(row) for row in signal_epochs_after_restart] == [
        ("rollback-signal-1", 0),
        ("rollback-signal-2", 0),
        ("signal-a", 1),
        ("signal-b", 1),
    ]
    assert "idx_signals_recipient_sequence" not in signal_indexes
    assert confirmation_expiry == 0
    assert service.is_authorized_participant("legacy-session", "legacy-owner") is False


def test_runtime_migration_parks_older_duplicate_v2_membership_once(public_service):
    older_pair = _public_media_pair(
        public_service,
        owner_media_version=0,
        guest_media_version=0,
    )
    older_session_id = older_pair["session"]["id"]
    newer_session = public_service.create_session(
        **{
            **older_pair["create_kwargs"],
            "title": "Newer runtime",
            "membership_capability": "T" * 43,
        }
    )
    newer_session_id = newer_session["id"]

    with public_service._db() as conn:
        conn.execute(
            "UPDATE sessions SET created_at = 10, owner_runtime_state = 'active' WHERE id = ?",
            (older_session_id,),
        )
        conn.execute(
            "UPDATE sessions SET created_at = 20, owner_runtime_state = 'active' WHERE id = ?",
            (newer_session_id,),
        )
        older_epoch = int(
            conn.execute(
                "SELECT security_epoch FROM sessions WHERE id = ?",
                (older_session_id,),
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT INTO signals (
                   id, session_id, sender_id, recipient_id, signal_type,
                   payload, sequence, security_epoch, sent_at
               ) VALUES (?, ?, ?, ?, 'offer', '{}', 1, ?, 11)""",
            (
                "pre-runtime-signal",
                older_session_id,
                older_pair["owner_peer_id"],
                older_pair["guest_peer_id"],
                older_epoch,
            ),
        )
        conn.execute(
            """INSERT INTO key_confirmations (
                   session_id, epoch, sender_peer_id, recipient_peer_id,
                   package_id, confirmation_tag, created_at, expires_at
               ) VALUES (?, ?, ?, ?, ?, ?, 11, 9999999999)""",
            (
                older_session_id,
                older_epoch,
                older_pair["owner_peer_id"],
                older_pair["guest_peer_id"],
                "a" * 64,
                base64.b64encode(b"m" * 32).decode("ascii"),
            ),
        )

        public_service._migrate_database(conn)
        first_migration = {
            row["id"]: (row["owner_runtime_state"], int(row["security_epoch"]))
            for row in conn.execute(
                """SELECT id, owner_runtime_state, security_epoch
                   FROM sessions WHERE id IN (?, ?)""",
                (older_session_id, newer_session_id),
            ).fetchall()
        }
        first_artifact_counts = tuple(
            conn.execute(
                """SELECT
                       (SELECT COUNT(1) FROM signals WHERE session_id = ?),
                       (SELECT COUNT(1) FROM key_confirmations WHERE session_id = ?)""",
                (older_session_id, older_session_id),
            ).fetchone()
        )

        public_service._migrate_database(conn)
        second_migration = {
            row["id"]: (row["owner_runtime_state"], int(row["security_epoch"]))
            for row in conn.execute(
                """SELECT id, owner_runtime_state, security_epoch
                   FROM sessions WHERE id IN (?, ?)""",
                (older_session_id, newer_session_id),
            ).fetchall()
        }

    assert first_migration[older_session_id] == ("parked", older_epoch + 1)
    assert first_migration[newer_session_id][0] == "active"
    assert first_artifact_counts == (0, 0)
    assert second_migration == first_migration


def test_pre_v2_strict_session_is_backfilled_as_account_scoped_v1(public_service, monkeypatch, tmp_path):
    database = tmp_path / "pre-v2-strict-rendezvous.db"
    _create_legacy_database(database)
    owner_id = _peer_id("legacy-owner-sub")
    owner_spki, owner_fp = _device_key()
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            ALTER TABLE sessions ADD COLUMN owner_device_id TEXT NOT NULL DEFAULT 'owner-device';
            ALTER TABLE sessions ADD COLUMN owner_public_key_spki_b64 TEXT NOT NULL DEFAULT '';
            ALTER TABLE sessions ADD COLUMN security_epoch INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE sessions ADD COLUMN security_mode TEXT NOT NULL DEFAULT 'legacy';
            ALTER TABLE sessions ADD COLUMN security_contract_version INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE participants ADD COLUMN public_key_spki_b64 TEXT NOT NULL DEFAULT '';
            """
        )
        conn.execute(
            """UPDATE sessions
               SET owner_user_id = ?, owner_device_fingerprint = ?,
                   owner_public_key_spki_b64 = ?, security_mode = 'strict_e2ee',
                   security_contract_version = 1
               WHERE id = 'legacy-session'""",
            (owner_id, owner_fp, owner_spki),
        )

    with sqlite3.connect(database, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        public_service._migrate_database(conn)
        migrated = conn.execute(
            """SELECT identity_binding_version, owner_account_id, owner_peer_id
               FROM sessions WHERE id = 'legacy-session'"""
        ).fetchone()

    assert tuple(migrated) == (1, owner_id, owner_id)
    monkeypatch.setattr(public_service.cfg, "RENDEZVOUS_DB_PATH", str(database))
    packages = public_service.get_key_packages(
        session_id="legacy-session",
        requester_user_id=owner_id,
    )
    assert packages["ok"] is True
    assert packages["local_peer_id"] == owner_id
    assert packages["packages"] == []


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
        participant_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(participants)")}
        signal_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(signals)")}

    assert "identity_binding_version" not in session_columns
    assert "public_media_e2ee_version" not in session_columns
    assert "public_media_e2ee_version" not in participant_columns
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


def test_http_guest_leave_requires_exact_membership_and_is_idempotent(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "http-leave-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    pair = _public_media_pair(
        public_app.svc,
        owner_media_version=2,
        guest_media_version=2,
    )
    # _public_media_pair derives its own deterministic subject; use an auth
    # context for that exact account before exercising the HTTP boundary.
    context = public_app.AuthContext(
        sub="media-pair-2-2",
        username="shared-account",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    path = f"/rendezvous/sessions/{pair['session']['id']}/membership"
    auth = {"Authorization": "Bearer test"}
    owner_headers = {
        **auth,
        "X-Ananta-Peer-Id": pair["owner_peer_id"],
        "X-Ananta-Membership-Capability": pair["owner_capability"],
    }
    guest_headers = {
        **auth,
        "X-Ananta-Peer-Id": pair["guest_peer_id"],
        "X-Ananta-Membership-Capability": pair["guest_capability"],
    }
    forged_headers = {
        **guest_headers,
        "X-Ananta-Membership-Capability": "Z" * 43,
    }
    client = public_app.app.test_client()

    owner_attempt = client.delete(path, headers=owner_headers)
    forged_attempt = client.delete(path, headers=forged_headers)
    left = client.delete(path, headers=guest_headers)
    repeated = client.delete(path, headers=guest_headers)

    assert (owner_attempt.status_code, owner_attempt.get_json()) == (
        409,
        {"error": "owner_must_end_session"},
    )
    assert (forged_attempt.status_code, forged_attempt.get_json()) == (
        403,
        {"error": "membership_capability_invalid"},
    )
    assert (left.status_code, left.get_json()) == (
        200,
        {
            "ok": True,
            "local_peer_id": pair["guest_peer_id"],
            "idempotent": False,
        },
    )
    assert (repeated.status_code, repeated.get_json()) == (
        200,
        {
            "ok": True,
            "local_peer_id": pair["guest_peer_id"],
            "idempotent": True,
        },
    )
    assert left.headers["Cache-Control"] == "no-store"


def test_http_v2_same_account_pair_requires_membership_capabilities(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "http-v2-rendezvous.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="shared-sub",
        username="shared-account",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    monkeypatch.setattr(public_app.cfg, "RATE_CREATE_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_JOIN_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_RECOVERY_PROBE_LIMIT", 10)
    monkeypatch.setattr(public_app.cfg, "RATE_MEMBERSHIP_PROBE_LIMIT", 20)
    monkeypatch.setattr(public_app.cfg, "RATE_SIGNAL_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_SIGNAL_POLL_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_TURN_CREDENTIAL_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "TURN_SHARED_SECRET", "turn-only-test-secret")
    monkeypatch.setattr(public_app.cfg, "TURN_URLS", ["turn:relay.example:3478"])
    monkeypatch.setattr(public_app.cfg, "CORS_ALLOWED_ORIGINS", {"https://app.example"})
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    owner_capability = "H" * 43
    guest_capability = "I" * 43
    media_capabilities = _public_media_capabilities_v1()
    expires_at = public_app.svc._now() + 600
    client = public_app.app.test_client()
    auth = {"Authorization": "Bearer shared"}
    owner_headers = {
        **auth,
        "X-Ananta-Membership-Capability": owner_capability,
        "Origin": "https://app.example",
    }
    create_body = {
        "title": "Shared account pair",
        "security_mode": "strict_e2ee",
        "security_contract_version": 1,
        "identity_binding_version": 2,
        "mode": "p2p",
        "transport": "webrtc",
        "expires_at": expires_at,
        "owner_device_id": "owner-device",
        "owner_device_fingerprint": owner_fp,
        "public_key_spki_b64": owner_spki,
        "public_media_e2ee_version": 1,
        "public_media_capabilities": media_capabilities,
    }

    info = client.get("/info")
    missing_media_capabilities = client.post(
        "/rendezvous/sessions",
        headers=owner_headers,
        json={key: value for key, value in create_body.items() if key != "public_media_capabilities"},
    )
    reordered_media_grants = client.post(
        "/rendezvous/sessions",
        headers=owner_headers,
        json={
            **create_body,
            "public_media_capabilities": {
                **media_capabilities,
                "grants": list(reversed(media_capabilities["grants"])),
            },
        },
    )
    invalid_v2_frame_format = client.post(
        "/rendezvous/sessions",
        headers=owner_headers,
        json={
            **create_body,
            "public_media_e2ee_version": 2,
            "public_media_capabilities": {
                **_public_media_capabilities_v2(),
                "frame_format": "ananta.public-pair.media-frame.v1",
            },
        },
    )
    created = client.post("/rendezvous/sessions", headers=owner_headers, json=create_body)
    recovered_create = client.post(
        "/rendezvous/sessions",
        headers=owner_headers,
        json=create_body,
    )
    created_payload = created.get_json()
    session_id = created_payload["session"]["id"]
    invite_code = created_payload["session"]["invite_code"]
    owner_peer_id = created_payload["local_peer_id"]
    assert created.status_code == 201
    assert created_payload["session"]["local_role"] == "owner"
    assert created_payload["session"]["local_runtime_state"] == "active"
    assert info.get_json()["supported_public_media_e2ee_versions"] == [1, 2]
    assert (missing_media_capabilities.status_code, missing_media_capabilities.get_json()) == (
        400,
        {"error": "public_media_capabilities_invalid"},
    )
    assert (reordered_media_grants.status_code, reordered_media_grants.get_json()) == (
        400,
        {"error": "public_media_capabilities_invalid"},
    )
    assert (invalid_v2_frame_format.status_code, invalid_v2_frame_format.get_json()) == (
        400,
        {"error": "public_media_capabilities_invalid"},
    )
    assert recovered_create.status_code == 200
    assert recovered_create.get_json()["session"]["id"] == session_id
    assert created.headers["Cache-Control"] == "no-store"
    assert "X-Ananta-Membership-Capability" in created.headers["Access-Control-Allow-Headers"]
    assert owner_capability not in json.dumps(created_payload)

    join_body = {
        "invite_code": invite_code,
        "minimum_security_mode": "strict_e2ee",
        "identity_binding_version": 2,
        "device_id": "guest-device",
        "device_fingerprint": guest_fp,
        "public_key_spki_b64": guest_spki,
        "public_media_e2ee_version": 1,
        "public_media_capabilities": media_capabilities,
    }
    guest_capability_header = {"X-Ananta-Membership-Capability": guest_capability}
    joined = client.post(
        "/rendezvous/sessions/join",
        headers={**auth, **guest_capability_header},
        json=join_body,
    )
    recovered_join = client.post(
        "/rendezvous/sessions/join",
        headers={**auth, **guest_capability_header},
        json=join_body,
    )
    joined_payload = joined.get_json()
    guest_peer_id = joined_payload["local_peer_id"]
    assert joined.status_code == 201
    assert recovered_join.status_code == 200
    assert joined.headers["Cache-Control"] == "no-store"
    assert guest_peer_id != owner_peer_id
    assert guest_capability not in json.dumps(joined_payload)
    assert joined_payload["session"]["local_role"] == "participant"
    assert joined_payload["session"]["local_runtime_state"] == "active"

    guest_headers = {
        **auth,
        "X-Ananta-Peer-Id": guest_peer_id,
        **guest_capability_header,
    }
    forged_headers = {
        **auth,
        "X-Ananta-Peer-Id": owner_peer_id,
        **guest_capability_header,
    }
    owner_bound_headers = {
        **auth,
        "X-Ananta-Peer-Id": owner_peer_id,
        "X-Ananta-Membership-Capability": owner_capability,
        "Origin": "https://app.example",
    }
    parked = client.put(
        f"/rendezvous/sessions/{session_id}/membership/runtime",
        headers=guest_headers,
        json={"state": "parked"},
    )
    parked_packages = client.get(
        f"/rendezvous/sessions/{session_id}/security/key-packages",
        headers=owner_bound_headers,
    )
    activated = client.put(
        f"/rendezvous/sessions/{session_id}/membership/runtime",
        headers=guest_headers,
        json={"state": "active"},
    )
    repeated_activation = client.put(
        f"/rendezvous/sessions/{session_id}/membership/runtime",
        headers=guest_headers,
        json={"state": "active"},
    )
    forged_activation = client.put(
        f"/rendezvous/sessions/{session_id}/membership/runtime",
        headers=forged_headers,
        json={"state": "active"},
    )
    parked_payload = parked.get_json()
    activated_payload = activated.get_json()
    assert parked.status_code == 200
    assert parked.headers["Cache-Control"] == "no-store"
    assert parked_payload["local_peer_id"] == guest_peer_id
    assert parked_payload["data"]["state"] == "parked"
    assert parked_payload["data"]["changed"] is True
    assert parked_payload["data"]["parked_session_ids"] == [session_id]
    assert parked_packages.status_code == 200
    assert parked_packages.get_json()["transport_ready"] is False
    assert parked_packages.get_json()["packages"] == []
    assert parked_packages.get_json()["security_contract"] is None
    assert activated.status_code == 200
    assert activated_payload["data"]["state"] == "active"
    assert activated_payload["data"]["security_epoch"] > parked_payload["data"]["security_epoch"]
    assert repeated_activation.status_code == 200
    assert repeated_activation.get_json()["data"] == {
        "state": "active",
        "security_epoch": activated_payload["data"]["security_epoch"],
        "changed": False,
        "parked_session_ids": [],
    }
    assert (forged_activation.status_code, forged_activation.get_json()) == (
        403,
        {"error": "membership_capability_invalid"},
    )
    participants = client.get(
        f"/rendezvous/sessions/{session_id}/participants",
        headers=guest_headers,
    )
    forged = client.get(
        f"/rendezvous/sessions/{session_id}/security/key-packages",
        headers=forged_headers,
    )
    assert participants.status_code == 200
    assert participants.get_json()["local_peer_id"] == guest_peer_id
    assert (forged.status_code, forged.get_json()) == (
        403,
        {"error": "membership_capability_invalid"},
    )

    owner_packages = client.get(
        f"/rendezvous/sessions/{session_id}/security/key-packages",
        headers=owner_bound_headers,
    )
    guest_packages = client.get(
        f"/rendezvous/sessions/{session_id}/security/key-packages",
        headers=guest_headers,
    )
    assert owner_packages.status_code == 200
    assert guest_packages.status_code == 200
    owner_packages_payload = owner_packages.get_json()
    guest_packages_payload = guest_packages.get_json()
    epoch = owner_packages_payload["epoch"]
    media_contract = owner_packages_payload["public_media_security_contract_v1"]
    assert media_contract["base_security_contract_digest"] == owner_packages_payload["security_contract_digest"]
    _verify_public_media_contract(media_contract, owner_packages_payload["hub_public_key_b64"])
    owner_confirmation = client.post(
        f"/rendezvous/sessions/{session_id}/security/key-confirmations",
        headers=owner_bound_headers,
        json={
            "recipient_peer_id": guest_peer_id,
            "package_id": owner_packages_payload["packages"][0]["package_id"],
            "epoch": epoch,
            "confirmation_tag": base64.b64encode(b"o" * 32).decode("ascii"),
        },
    )
    guest_confirmation = client.post(
        f"/rendezvous/sessions/{session_id}/security/key-confirmations",
        headers=guest_headers,
        json={
            "recipient_peer_id": owner_peer_id,
            "package_id": guest_packages_payload["packages"][0]["package_id"],
            "epoch": epoch,
            "confirmation_tag": base64.b64encode(b"g" * 32).decode("ascii"),
        },
    )
    assert owner_confirmation.status_code == 201
    assert guest_confirmation.status_code == 201
    owner_signal = client.post(
        f"/webrtc/sessions/{session_id}/signal",
        headers=owner_bound_headers,
        json={
            "type": "offer",
            "sender_id": owner_peer_id,
            "recipient_id": guest_peer_id,
            "payload": {"sdp": "opaque-owner"},
            "security_epoch": epoch,
        },
    )
    guest_signal = client.post(
        f"/webrtc/sessions/{session_id}/signal",
        headers=guest_headers,
        json={
            "type": "answer",
            "sender_id": guest_peer_id,
            "recipient_id": owner_peer_id,
            "payload": {"sdp": "opaque-guest"},
            "security_epoch": epoch,
        },
    )
    owner_signal_limited = client.post(
        f"/webrtc/sessions/{session_id}/signal",
        headers=owner_bound_headers,
        json={
            "type": "offer",
            "sender_id": owner_peer_id,
            "recipient_id": guest_peer_id,
            "payload": {"sdp": "second-owner-offer"},
            "security_epoch": epoch,
        },
    )
    assert owner_signal.status_code == 201
    assert guest_signal.status_code == 201
    assert (owner_signal_limited.status_code, owner_signal_limited.get_json()) == (
        429,
        {"error": "rate_limited"},
    )
    assert owner_signal_limited.headers["Retry-After"].isdigit()
    assert owner_signal_limited.headers["Access-Control-Expose-Headers"] == "Retry-After"

    owner_poll = client.get(
        f"/webrtc/sessions/{session_id}/signal?security_epoch={epoch}",
        headers=owner_bound_headers,
    )
    guest_poll = client.get(
        f"/webrtc/sessions/{session_id}/signal?security_epoch={epoch}",
        headers=guest_headers,
    )
    owner_poll_limited = client.get(
        f"/webrtc/sessions/{session_id}/signal?security_epoch={epoch}",
        headers=owner_bound_headers,
    )
    owner_turn = client.get(
        f"/rendezvous/turn-credentials?session_id={session_id}",
        headers=owner_bound_headers,
    )
    guest_turn = client.get(
        f"/rendezvous/turn-credentials?session_id={session_id}",
        headers=guest_headers,
    )
    owner_turn_limited = client.get(
        f"/rendezvous/turn-credentials?session_id={session_id}",
        headers=owner_bound_headers,
    )
    assert owner_poll.status_code == 200
    assert guest_poll.status_code == 200
    assert (owner_poll_limited.status_code, owner_poll_limited.get_json()) == (
        429,
        {"error": "rate_limited"},
    )
    assert owner_turn.status_code == 200
    assert guest_turn.status_code == 200
    assert (owner_turn_limited.status_code, owner_turn_limited.get_json()) == (
        429,
        {"error": "rate_limited"},
    )
    assert owner_turn.get_json()["data"]["username"] != guest_turn.get_json()["data"]["username"]

    empty_catalog = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={"memberships": []},
    )
    cross_device_proof = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={
            "memberships": [
                {
                    "session_id": session_id,
                    "local_peer_id": owner_peer_id,
                    "membership_capability": guest_capability,
                }
            ]
        },
    )
    malformed_catalog = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={"memberships": [{"session_id": session_id}]},
    )
    duplicate_catalog = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={
            "memberships": [
                {
                    "session_id": session_id,
                    "local_peer_id": guest_peer_id,
                    "membership_capability": guest_capability,
                },
                {
                    "session_id": session_id,
                    "local_peer_id": guest_peer_id,
                    "membership_capability": guest_capability,
                },
            ]
        },
    )
    oversized_catalog = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={
            "memberships": [
                {
                    "session_id": session_id,
                    "local_peer_id": guest_peer_id,
                    "membership_capability": guest_capability,
                }
                for _ in range(33)
            ]
        },
    )
    assert empty_catalog.status_code == 200
    assert empty_catalog.headers["Cache-Control"] == "no-store"
    assert empty_catalog.get_json() == {"ok": True, "data": {"items": []}}
    assert cross_device_proof.status_code == 200
    assert cross_device_proof.get_json() == {"ok": True, "data": {"items": []}}
    assert (malformed_catalog.status_code, malformed_catalog.get_json()) == (
        400,
        {"error": "catalog_request_invalid"},
    )
    assert (duplicate_catalog.status_code, duplicate_catalog.get_json()) == (
        400,
        {"error": "catalog_request_invalid"},
    )
    assert (oversized_catalog.status_code, oversized_catalog.get_json()) == (
        400,
        {"error": "catalog_request_invalid"},
    )

    legacy_spki, legacy_fp = _device_key()
    legacy_session = public_app.svc.create_session(
        owner_user_id=context.account_id,
        owner_user_sub=context.sub,
        owner_device_id="legacy-device",
        owner_device_fingerprint=legacy_fp,
        owner_public_key_spki_b64=legacy_spki,
        oidc_issuer=context.issuer,
        title="Legacy v1 pair",
        identity_binding_version=1,
    )
    legacy_list = client.get(
        "/rendezvous/sessions",
        headers={**auth, "X-Ananta-Peer-Id": guest_peer_id, "X-Ananta-Device-Id": "guest-device"},
    )
    guest_list = client.post(
        "/rendezvous/sessions/catalog",
        headers=auth,
        json={
            "memberships": [
                {
                    "session_id": session_id,
                    "local_peer_id": guest_peer_id,
                    "membership_capability": guest_capability,
                }
            ]
        },
    )
    legacy_payload = legacy_list.get_json()
    guest_list_payload = guest_list.get_json()
    assert [item["id"] for item in legacy_payload["data"]["items"]] == [legacy_session["id"]]
    assert legacy_payload["local_peer_id"] == context.account_id
    assert legacy_payload["data"]["local_peer_id"] == context.account_id
    assert legacy_list.headers["Cache-Control"] == "no-store"
    assert guest_list.headers["Cache-Control"] == "no-store"
    assert all(item["local_role"] == "owner" for item in legacy_payload["data"]["items"])
    assert {item["id"] for item in guest_list_payload["data"]["items"]} == {
        legacy_session["id"],
        session_id,
    }
    v2_item = next(item for item in guest_list_payload["data"]["items"] if item["identity_binding_version"] == 2)
    assert v2_item["local_peer_id"] == guest_peer_id
    assert v2_item["local_role"] == "participant"
    assert "invite_code" not in v2_item
    assert {
        "participants",
        "owner_user_id",
        "owner_user_sub_hash",
        "owner_account_id",
        "owner_device_id",
        "owner_device_fingerprint",
        "owner_public_key_spki_b64",
        "oidc_issuer",
    }.isdisjoint(v2_item)
    assert "membership_capability" not in json.dumps(guest_list_payload)
    assert owner_capability not in json.dumps(guest_list_payload)
    assert guest_capability not in json.dumps(guest_list_payload)


def test_create_recovery_probe_is_rate_limited_before_lookup(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "create-recovery-probe.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="probe-sub",
        username="probe-account",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    monkeypatch.setattr(public_app.cfg, "RATE_RECOVERY_PROBE_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_CREATE_LIMIT", 10)
    lookup_calls = []
    create_calls = []

    def lookup(**kwargs):
        lookup_calls.append(kwargs)
        return False

    def create(**kwargs):
        create_calls.append(kwargs)
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "owner_peer_id": "peer:" + "1" * 64,
            "identity_binding_version": 2,
        }

    monkeypatch.setattr(public_app.svc, "is_owner_create_recovery", lookup)
    monkeypatch.setattr(public_app.svc, "create_session", create)
    owner_spki, owner_fp = _device_key()
    body = {
        "identity_binding_version": 2,
        "owner_device_id": "probe-device",
        "owner_device_fingerprint": owner_fp,
        "public_key_spki_b64": owner_spki,
    }
    client = public_app.app.test_client()

    first = client.post(
        "/rendezvous/sessions",
        headers={
            "Authorization": "Bearer probe",
            "X-Ananta-Membership-Capability": "J" * 43,
        },
        json=body,
    )
    limited = client.post(
        "/rendezvous/sessions",
        headers={
            "Authorization": "Bearer probe",
            "X-Ananta-Membership-Capability": "K" * 43,
        },
        json=body,
    )

    assert first.status_code == 201
    assert (limited.status_code, limited.get_json()) == (429, {"error": "rate_limited"})
    assert limited.headers["Retry-After"].isdigit()
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.headers["Cache-Control"] == "no-store"
    assert [call["membership_capability"] for call in lookup_calls] == ["J" * 43]
    assert len(create_calls) == 1


def test_rate_limit_bucket_is_shared_across_service_process_state(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "shared-rate-limit.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service"):
        sys.modules.pop(module_name, None)
    first_service = importlib.import_module("service")

    assert first_service._rate_check_with_retry("join", "account", 1, 60) == (True, 0)

    sys.modules.pop("service", None)
    second_service = importlib.import_module("service")
    allowed, retry_after = second_service._rate_check_with_retry("join", "account", 1, 60)

    assert allowed is False
    assert 1 <= retry_after <= 60


def test_join_recovery_probe_is_rate_limited_before_lookup(monkeypatch, tmp_path):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / "join-recovery-probe.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="probe-sub",
        username="probe-account",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    monkeypatch.setattr(public_app.cfg, "RATE_RECOVERY_PROBE_LIMIT", 1)
    monkeypatch.setattr(public_app.cfg, "RATE_JOIN_LIMIT", 10)
    lookup_calls = []
    join_calls = []

    def lookup(**kwargs):
        lookup_calls.append(kwargs)
        return False

    def join(**kwargs):
        join_calls.append(kwargs)
        return {"ok": False, "reason": "invalid_invite_code"}

    monkeypatch.setattr(public_app.svc, "is_join_recovery", lookup)
    monkeypatch.setattr(public_app.svc, "join_session", join)
    guest_spki, guest_fp = _device_key()
    body = {
        "invite_code": "AAAA-BBBB",
        "identity_binding_version": 2,
        "device_id": "probe-device",
        "device_fingerprint": guest_fp,
        "public_key_spki_b64": guest_spki,
    }
    client = public_app.app.test_client()

    first = client.post(
        "/rendezvous/sessions/join",
        headers={
            "Authorization": "Bearer probe",
            "X-Ananta-Membership-Capability": "L" * 43,
        },
        json=body,
    )
    limited = client.post(
        "/rendezvous/sessions/join",
        headers={
            "Authorization": "Bearer probe",
            "X-Ananta-Membership-Capability": "M" * 43,
        },
        json=body,
    )

    assert (first.status_code, first.get_json()) == (400, {"error": "invalid_invite_code"})
    assert (limited.status_code, limited.get_json()) == (429, {"error": "rate_limited"})
    assert [call["membership_capability"] for call in lookup_calls] == ["L" * 43]
    assert len(join_calls) == 1


@pytest.mark.parametrize("endpoint", ("turn", "signal_push", "signal_poll"))
def test_membership_probe_is_rate_limited_before_resolver(monkeypatch, tmp_path, endpoint):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(tmp_path / f"{endpoint}-membership-probe.db"))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "peer_identity", "service", "oidc_auth", "app"):
        sys.modules.pop(module_name, None)
    public_app = importlib.import_module("app")
    context = public_app.AuthContext(
        sub="probe-sub",
        username="probe-account",
        issuer="https://issuer",
        raw={},
    )
    monkeypatch.setattr(public_app, "verify_bearer_token", lambda _header: context)
    monkeypatch.setattr(public_app.cfg, "RATE_MEMBERSHIP_PROBE_LIMIT", 1)
    resolver_calls = []

    def resolve(**kwargs):
        resolver_calls.append(kwargs)
        return {"ok": False, "reason": "membership_capability_invalid"}

    monkeypatch.setattr(public_app.svc, "authenticate_session_membership", resolve)
    session_id = "00000000-0000-0000-0000-000000000002"
    client = public_app.app.test_client()

    def probe(capability: str, peer_digit: str):
        headers = {
            "Authorization": "Bearer probe",
            "X-Ananta-Peer-Id": "peer:" + peer_digit * 64,
            "X-Ananta-Membership-Capability": capability,
        }
        if endpoint == "turn":
            return client.get(
                f"/rendezvous/turn-credentials?session_id={session_id}",
                headers=headers,
            )
        if endpoint == "signal_push":
            return client.post(
                f"/webrtc/sessions/{session_id}/signal",
                headers=headers,
                json={
                    "type": "offer",
                    "recipient_id": "peer:" + "3" * 64,
                    "payload": {"sdp": "opaque"},
                },
            )
        return client.get(f"/webrtc/sessions/{session_id}/signal", headers=headers)

    first = probe("N" * 43, "1")
    limited = probe("O" * 43, "2")

    assert (first.status_code, first.get_json()) == (
        403,
        {"error": "membership_capability_invalid"},
    )
    assert (limited.status_code, limited.get_json()) == (429, {"error": "rate_limited"})
    assert len(resolver_calls) == 1
    assert resolver_calls[0]["account_id"] == context.account_id


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
    public_app.svc.reset_rate_limits_for_tests()
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
    public_app.svc.reset_rate_limits_for_tests()
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

    ended = client.delete(
        f"/rendezvous/sessions/{session['id']}",
        headers=owner_headers,
    )
    replacement, _, _ = _joined_session(public_app.svc)
    replacement_first = client.get(
        f"/rendezvous/turn-credentials?session_id={replacement['id']}",
        headers=owner_headers,
    )

    assert ended.status_code == 200
    assert replacement_first.status_code == 200
    assert replacement_first.get_json()["session_id"] == replacement["id"]


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
    public_app.svc.reset_rate_limits_for_tests()
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


def _public_media_capabilities_v1() -> dict:
    return {
        "version": 1,
        "transform": "RTCRtpScriptTransform",
        "grants": ["microphone-opus", "camera-vp8", "screen-vp8"],
    }


def _public_media_capabilities_v2() -> dict:
    return {
        "version": 2,
        "transform": "RTCRtpScriptTransform",
        "frame_format": "ananta.public-pair.media-frame.v2",
        "grants": ["microphone-opus", "camera-vp8", "screen-vp8"],
    }


def _public_media_capabilities_for_version(version: int) -> dict | None:
    if version == 1:
        return _public_media_capabilities_v1()
    if version == 2:
        return _public_media_capabilities_v2()
    return None


def _public_media_pair(public_service, *, owner_media_version: int, guest_media_version: int) -> dict:
    subject = f"media-pair-{owner_media_version}-{guest_media_version}"
    account_id = _peer_id(subject)
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    owner_capability = "R" * 43
    guest_capability = "S" * 43
    create_kwargs = {
        "owner_user_id": account_id,
        "owner_user_sub": subject,
        "owner_device_id": "media-owner-device",
        "owner_device_fingerprint": owner_fp,
        "owner_public_key_spki_b64": owner_spki,
        "oidc_issuer": "https://issuer",
        "identity_binding_version": 2,
        "membership_capability": owner_capability,
        "public_media_e2ee_version": owner_media_version,
        "public_media_capabilities": _public_media_capabilities_for_version(owner_media_version),
    }
    session = public_service.create_session(**create_kwargs)
    join_kwargs = {
        "invite_code": session["invite_code"],
        "user_id": account_id,
        "user_sub": subject,
        "device_id": "media-guest-device",
        "device_fingerprint": guest_fp,
        "public_key_spki_b64": guest_spki,
        "oidc_issuer": "https://issuer",
        "expected_identity_binding_version": 2,
        "membership_capability": guest_capability,
        "public_media_e2ee_version": guest_media_version,
        "public_media_capabilities": _public_media_capabilities_for_version(guest_media_version),
    }
    joined = public_service.join_session(**join_kwargs)
    assert joined["ok"] is True
    owner_peer_id = session["owner_peer_id"]
    guest_peer_id = joined["participant"]["peer_id"]
    owner_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=owner_peer_id,
        membership_capability=owner_capability,
    )
    guest_packages = public_service.get_key_packages(
        session_id=session["id"],
        requester_user_id=account_id,
        requester_peer_id=guest_peer_id,
        membership_capability=guest_capability,
    )
    return {
        "account_id": account_id,
        "session": session,
        "joined": joined,
        "owner_peer_id": owner_peer_id,
        "guest_peer_id": guest_peer_id,
        "owner_fingerprint": owner_fp,
        "guest_fingerprint": guest_fp,
        "owner_capability": owner_capability,
        "guest_capability": guest_capability,
        "create_kwargs": create_kwargs,
        "join_kwargs": join_kwargs,
        "owner_packages": owner_packages,
        "guest_packages": guest_packages,
    }


def _confirm_v2_transport(
    public_service,
    *,
    session_id: str,
    epoch: int,
    owner_account_id: str,
    owner_peer_id: str,
    owner_capability: str,
    guest_account_id: str,
    guest_peer_id: str,
    guest_capability: str,
) -> dict:
    owner_packages = public_service.get_key_packages(
        session_id=session_id,
        requester_user_id=owner_account_id,
        requester_peer_id=owner_peer_id,
        membership_capability=owner_capability,
    )
    guest_packages = public_service.get_key_packages(
        session_id=session_id,
        requester_user_id=guest_account_id,
        requester_peer_id=guest_peer_id,
        membership_capability=guest_capability,
    )
    assert owner_packages["epoch"] == epoch
    assert guest_packages["epoch"] == epoch
    assert owner_packages["packages"] and guest_packages["packages"]
    owner_confirmation = public_service.put_key_confirmation(
        session_id=session_id,
        sender_peer_id=owner_peer_id,
        recipient_peer_id=guest_peer_id,
        package_id=owner_packages["packages"][0]["package_id"],
        epoch=epoch,
        confirmation_tag=base64.b64encode(b"o" * 32).decode("ascii"),
        sender_account_id=owner_account_id,
        membership_capability=owner_capability,
    )
    guest_confirmation = public_service.put_key_confirmation(
        session_id=session_id,
        sender_peer_id=guest_peer_id,
        recipient_peer_id=owner_peer_id,
        package_id=guest_packages["packages"][0]["package_id"],
        epoch=epoch,
        confirmation_tag=base64.b64encode(b"g" * 32).decode("ascii"),
        sender_account_id=guest_account_id,
        membership_capability=guest_capability,
    )
    assert owner_confirmation["ok"] is True
    assert guest_confirmation["ok"] is True
    return {"owner": owner_packages, "guest": guest_packages}


def _verify_public_media_contract(contract: dict, public_key_b64: str, *, check_digest: bool = True) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signed = dict(contract)
    signature = base64.b64decode(signed.pop("signature_b64"), validate=True)
    digest_projection = dict(signed)
    digest_projection.pop("digest")
    digest_projection.pop("signature_algorithm")
    expected_digest = hashlib.sha256(
        json.dumps(
            digest_projection,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    if check_digest:
        assert signed["digest"] == expected_digest
    canonical = json.dumps(signed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
    public_key.verify(signature, canonical)


def _verify_package_signature(response: dict) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    package = dict(response["packages"][0])
    signature = base64.b64decode(package.pop("signature_b64"), validate=True)
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(response["hub_public_key_b64"]))
    public_key.verify(signature, canonical)
