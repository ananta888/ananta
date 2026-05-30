"""PRD03.02: Tests für die Rendezvous-API."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from agent.services.rendezvous_service import RendezvousService, get_rendezvous_service


@pytest.fixture(autouse=True)
def reset_rendezvous_state():
    """Setzt den in-memory State vor jedem Test zurück."""
    import agent.services.rendezvous_service as svc
    svc._sessions.clear()
    svc._participants.clear()
    svc._invite_codes.clear()
    svc._service = None
    yield
    svc._sessions.clear()
    svc._participants.clear()
    svc._invite_codes.clear()
    svc._service = None


@pytest.fixture
def service():
    return RendezvousService()


def test_create_session_returns_invite_code(service):
    result = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp:aabb:ccdd",
        oidc_issuer="https://keycloak.ananta.de/realms/ananta",
        title="Test Session",
    )
    assert result["invite_code"]
    assert len(result["invite_code"]) == 10
    assert result["id"]
    assert result["oidc_issuer"] == "https://keycloak.ananta.de/realms/ananta"


def test_create_session_defaults_permissions_no_remote_control(service):
    result = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    perms = result["allowed_permissions"]
    assert perms["chat"] is True
    assert perms["view_tui"] is False
    assert perms["remote_control"] is False


def test_join_session_valid_invite(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="https://test.issuer",
    )
    code = session["invite_code"]
    result = service.join_session(
        invite_code=code,
        user_id="user-b",
        user_sub="sub-b",
        device_id="device-b",
        device_fingerprint="fp-b",
        oidc_issuer="https://test.issuer",
    )
    assert result["ok"]
    assert result["participant"]["user_id"] == "user-b"


def test_join_session_invalid_code(service):
    result = service.join_session(
        invite_code="INVALID99",
        user_id="user-b",
        user_sub="sub-b",
        device_id="device-b",
        device_fingerprint="fp-b",
        oidc_issuer="",
    )
    assert not result["ok"]
    assert result["reason"] == "invalid_invite_code"


def test_join_session_without_oidc_sub_fails(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    code = session["invite_code"]
    result = service.join_session(
        invite_code=code,
        user_id="user-b",
        user_sub="",  # kein Sub
        device_id="device-b",
        device_fingerprint="fp-b",
        oidc_issuer="",
    )
    assert not result["ok"]
    assert result["reason"] == "oidc_sub_required"


def test_join_session_oidc_issuer_mismatch(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="https://correct.issuer",
    )
    code = session["invite_code"]
    result = service.join_session(
        invite_code=code,
        user_id="user-b",
        user_sub="sub-b",
        device_id="device-b",
        device_fingerprint="fp-b",
        oidc_issuer="https://wrong.issuer",
    )
    assert not result["ok"]
    assert result["reason"] == "oidc_issuer_mismatch"


def test_join_session_idempotent(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    code = session["invite_code"]
    r1 = service.join_session(invite_code=code, user_id="b", user_sub="sb", device_id="d", device_fingerprint="fp", oidc_issuer="")
    r2 = service.join_session(invite_code=code, user_id="b", user_sub="sb", device_id="d", device_fingerprint="fp", oidc_issuer="")
    assert r1["ok"]
    assert r2["ok"]
    assert r2.get("idempotent")


def test_get_participants_only_for_members(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    sid = session["id"]
    code = session["invite_code"]
    service.join_session(invite_code=code, user_id="user-b", user_sub="sb", device_id="d", device_fingerprint="fp", oidc_issuer="")
    # Owner darf abrufen
    result = service.get_participants(session_id=sid, requester_user_id="user-a")
    assert result["ok"]
    assert len(result["participants"]) == 1
    # Fremder darf nicht
    result2 = service.get_participants(session_id=sid, requester_user_id="user-x")
    assert not result2["ok"]
    assert result2["reason"] == "forbidden"


def test_revoke_session_by_owner(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    sid = session["id"]
    result = service.revoke_session(session_id=sid, actor_user_id="user-a")
    assert result["ok"]


def test_revoke_session_by_non_owner_fails(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    sid = session["id"]
    result = service.revoke_session(session_id=sid, actor_user_id="user-b")
    assert not result["ok"]
    assert result["reason"] == "forbidden"


def test_join_revoked_session_fails(service):
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a",
        oidc_issuer="",
    )
    sid = session["id"]
    code = session["invite_code"]
    service.revoke_session(session_id=sid, actor_user_id="user-a")
    result = service.join_session(invite_code=code, user_id="user-b", user_sub="sb", device_id="d", device_fingerprint="fp", oidc_issuer="")
    assert not result["ok"]
    # Code wurde entfernt
    assert result["reason"] == "invalid_invite_code"
