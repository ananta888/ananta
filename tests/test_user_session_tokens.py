from __future__ import annotations

import pytest

from agent.config import settings
from agent.services.user_session_tokens import (
    UserSessionIdentityError,
    build_user_access_token_claims,
    local_user_tenant_id,
)


def test_user_access_claims_preserve_existing_canonical_local_tenant_identity() -> None:
    claims = build_user_access_token_claims(
        username="local-operator",
        role="user",
        mfa_enabled=True,
        issued_at=1234,
    )

    assert claims == {
        "sub": "local-operator",
        "tenant_id": "local-operator",
        "role": "user",
        "mfa_enabled": True,
        "iat": 1234,
        "exp": 1234 + settings.auth_access_token_ttl_seconds,
    }


def test_local_user_tenant_identity_is_deterministic_and_fails_closed() -> None:
    assert local_user_tenant_id("alice") == "alice"
    assert local_user_tenant_id("alice") != local_user_tenant_id("Alice")
    with pytest.raises(UserSessionIdentityError, match="user_session_username_not_canonical"):
        local_user_tenant_id("   ")


@pytest.mark.parametrize(
    ("username", "reason_code"),
    [
        (" alice", "user_session_username_not_canonical"),
        ("alice ", "user_session_username_not_canonical"),
        ("alice\nadmin", "user_session_username_not_canonical"),
        ("a" * 161, "user_session_username_too_long"),
    ],
)
def test_user_access_claims_reject_colliding_or_oversized_usernames(
    username: str,
    reason_code: str,
) -> None:
    with pytest.raises(UserSessionIdentityError, match=reason_code):
        build_user_access_token_claims(
            username=username,
            role="user",
            issued_at=1234,
        )
