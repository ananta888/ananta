"""PRD02.02: Tests für OIDC-Token-Bindung an Rendezvous-Join."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from agent.services.rendezvous_service import RendezvousService


@pytest.fixture(autouse=True)
def reset_state():
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


def test_join_requires_oidc_sub():
    svc = RendezvousService()
    session = svc.create_session(owner_user_id="u1", owner_device_fingerprint="fp", oidc_issuer="")
    code = session["invite_code"]
    result = svc.join_session(invite_code=code, user_id="u2", user_sub="", device_id="d", device_fingerprint="fp2", oidc_issuer="")
    assert not result["ok"]
    assert result["reason"] == "oidc_sub_required"


def test_join_issuer_must_match_session():
    svc = RendezvousService()
    session = svc.create_session(
        owner_user_id="u1",
        owner_device_fingerprint="fp",
        oidc_issuer="https://issuer-A",
    )
    code = session["invite_code"]
    result = svc.join_session(
        invite_code=code,
        user_id="u2",
        user_sub="sub-u2",
        device_id="d",
        device_fingerprint="fp2",
        oidc_issuer="https://issuer-B",  # falsch
    )
    assert not result["ok"]
    assert result["reason"] == "oidc_issuer_mismatch"


def test_join_with_correct_issuer_succeeds():
    svc = RendezvousService()
    session = svc.create_session(
        owner_user_id="u1",
        owner_device_fingerprint="fp",
        oidc_issuer="https://correct-issuer",
    )
    code = session["invite_code"]
    result = svc.join_session(
        invite_code=code,
        user_id="u2",
        user_sub="sub-u2",
        device_id="d",
        device_fingerprint="fp2",
        oidc_issuer="https://correct-issuer",
    )
    assert result["ok"]
    assert result["participant"]["user_sub"] == "sub-u2"


def test_user_sub_from_token_not_body():
    """user_sub muss aus Token kommen, nicht vertraut aus dem Body."""
    svc = RendezvousService()
    session = svc.create_session(owner_user_id="u1", owner_device_fingerprint="fp", oidc_issuer="")
    code = session["invite_code"]
    # Wenn user_id aus Body und user_sub aus Token verschieden, muss user_sub verwendet werden
    result = svc.join_session(
        invite_code=code,
        user_id="body-user-id",
        user_sub="real-oidc-sub",
        device_id="d",
        device_fingerprint="fp2",
        oidc_issuer="",
    )
    assert result["ok"]
    assert result["participant"]["user_sub"] == "real-oidc-sub"


def test_expired_session_rejected():
    import time
    svc = RendezvousService()
    session = svc.create_session(
        owner_user_id="u1",
        owner_device_fingerprint="fp",
        oidc_issuer="",
        expires_at=time.time() - 1,  # already expired
    )
    code = session["invite_code"]
    result = svc.join_session(
        invite_code=code,
        user_id="u2",
        user_sub="sub",
        device_id="d",
        device_fingerprint="fp2",
        oidc_issuer="",
    )
    assert not result["ok"]
    assert result["reason"] == "session_expired"
