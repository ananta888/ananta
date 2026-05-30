"""Tests for the standalone public rendezvous service contract."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def public_service(monkeypatch):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    sys.modules.pop("config", None)
    sys.modules.pop("service", None)
    service = importlib.import_module("service")
    service._sessions.clear()
    service._participants.clear()
    service._invite_codes.clear()
    service._rate_buckets.clear()
    yield service
    service._sessions.clear()
    service._participants.clear()
    service._invite_codes.clear()
    service._rate_buckets.clear()


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
