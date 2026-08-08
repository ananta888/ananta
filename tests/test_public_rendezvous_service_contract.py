"""Tests for the standalone public rendezvous service contract."""
from __future__ import annotations

import importlib
import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def public_service(monkeypatch):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RENDEZVOUS_DB_PATH", str(Path(__file__).resolve().parent / ".tmp-public-rendezvous-test.db"))
    monkeypatch.setenv("RENDEZVOUS_SECURITY_SIGNING_SECRET", "test-only-public-rendezvous-signing-secret-32-bytes")
    sys.modules.pop("config", None)
    sys.modules.pop("service", None)
    service = importlib.import_module("service")
    service.reset_state_for_tests()
    yield service
    service.reset_state_for_tests()


def test_list_sessions_returns_owner_and_participant_sessions(public_service):
    session = public_service.create_session(
        owner_user_id="owner",
        owner_user_sub="owner-sub",
        owner_device_fingerprint="owner-fp",
        oidc_issuer="https://issuer",
        title="Pairing",
    )
    public_service.join_session(
        invite_code=session["invite_code"],
        user_id="guest",
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint="guest-fp",
        oidc_issuer="https://issuer",
    )

    owner_items = public_service.list_sessions_for_user(requester_user_id="owner")
    guest_items = public_service.list_sessions_for_user(requester_user_id="guest")
    stranger_items = public_service.list_sessions_for_user(requester_user_id="stranger")

    assert [item["id"] for item in owner_items] == [session["id"]]
    assert [item["id"] for item in guest_items] == [session["id"]]
    assert stranger_items == []
    assert owner_items[0]["permissions"]["view_tui"] is False
    assert owner_items[0]["participant_count"] == 1


def test_join_can_be_bound_to_expected_session_id(public_service):
    session = public_service.create_session(
        owner_user_id="owner",
        owner_user_sub="owner-sub",
        owner_device_fingerprint="owner-fp",
        oidc_issuer="",
    )

    wrong = public_service.join_session(
        invite_code=session["invite_code"],
        user_id="guest",
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint="guest-fp",
        oidc_issuer="",
        expected_session_id="different-session",
    )
    right = public_service.join_session(
        invite_code=session["invite_code"],
        user_id="guest",
        user_sub="guest-sub",
        device_id="guest-device",
        device_fingerprint="guest-fp",
        oidc_issuer="",
        expected_session_id=session["id"],
    )

    assert wrong == {"ok": False, "reason": "session_not_found"}
    assert right["ok"] is True


def test_owner_can_update_view_permission(public_service):
    session = public_service.create_session(
        owner_user_id="owner",
        owner_user_sub="owner-sub",
        owner_device_fingerprint="owner-fp",
        oidc_issuer="",
    )

    result = public_service.update_session_permissions(
        session_id=session["id"],
        actor_user_id="owner",
        permissions={"view_tui": True, "remote_control": True},
    )
    forbidden = public_service.update_session_permissions(
        session_id=session["id"],
        actor_user_id="guest",
        permissions={"view_tui": False},
    )

    assert result["ok"] is True
    assert result["session"]["permissions"]["view_tui"] is True
    assert result["session"]["permissions"]["remote_control"] is False
    assert forbidden == {"ok": False, "reason": "forbidden"}


def test_strict_pair_uses_signed_addressed_packages_and_opaque_confirmations(public_service):
    owner_spki, owner_fp = _device_key()
    guest_spki, guest_fp = _device_key()
    session = public_service.create_session(
        owner_user_id="alice",
        owner_user_sub="alice-sub",
        owner_device_id="alice-device",
        owner_device_fingerprint=owner_fp,
        owner_public_key_spki_b64=owner_spki,
        oidc_issuer="https://issuer",
    )

    joined = public_service.join_session(
        invite_code=session["invite_code"],
        user_id="bob",
        user_sub="bob-sub",
        device_id="bob-device",
        device_fingerprint=guest_fp,
        public_key_spki_b64=guest_spki,
        oidc_issuer="https://issuer",
    )
    owner_packages = public_service.get_key_packages(
        session_id=session["id"], requester_user_id="alice",
    )
    guest_packages = public_service.get_key_packages(
        session_id=session["id"], requester_user_id="bob",
    )

    assert joined["session"]["security_epoch"] == 2
    assert owner_packages["packages"][0]["peer_id"] == "bob"
    assert owner_packages["packages"][0]["recipient_peer_id"] == "alice"
    assert guest_packages["packages"][0]["peer_id"] == "alice"
    assert owner_packages["security_contract_digest"] == guest_packages["security_contract_digest"]
    _verify_package_signature(owner_packages)

    tag = base64.b64encode(b"x" * 32).decode("ascii")
    stored = public_service.put_key_confirmation(
        session_id=session["id"], sender_peer_id="alice", recipient_peer_id="bob",
        package_id=owner_packages["packages"][0]["package_id"], epoch=2, confirmation_tag=tag,
    )
    fetched = public_service.get_key_confirmation(
        session_id=session["id"], requester_user_id="bob", sender_peer_id="alice",
    )
    assert stored == {"ok": True}
    assert fetched["confirmation"]["confirmation_tag"] == tag


def _device_key() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key

    raw = generate_private_key(SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _verify_package_signature(response: dict) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    package = dict(response["packages"][0])
    signature = base64.b64decode(package.pop("signature_b64"), validate=True)
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(response["hub_public_key_b64"]))
    public_key.verify(signature, canonical)
