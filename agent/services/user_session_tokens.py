from __future__ import annotations

import secrets
import time

import jwt
from sqlalchemy.exc import IntegrityError

from agent.config import settings
from agent.db_models import RefreshTokenDB
from agent.services.identity_validation import (
    DEFAULT_IDENTITY_MAX_LENGTH,
    IdentityValidationError,
    require_canonical_identity,
)
from agent.services.repository_registry import get_repository_registry

MAX_USER_SESSION_IDENTITY_LENGTH = DEFAULT_IDENTITY_MAX_LENGTH


class UserSessionIdentityError(ValueError):
    """Raised when a persisted username cannot safely become a tenant key."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def local_user_tenant_id(username: str) -> str:
    """Return the stable tenant boundary for one Hub-managed user account.

    Local accounts do not currently belong to an external organization. Their
    canonical Hub username was already the workflow tenant fallback before an
    explicit claim existed, so retaining it avoids orphaning existing runs.
    Identity values are never trimmed or truncated: doing so could merge two
    independently persisted user accounts into one tenant.
    """

    try:
        return require_canonical_identity(
            username,
            field_name="username",
            max_length=MAX_USER_SESSION_IDENTITY_LENGTH,
        )
    except IdentityValidationError as exc:
        raise UserSessionIdentityError(
            f"user_session_{exc.reason_code}"
        ) from exc


def build_user_access_token_claims(
    *,
    username: str,
    role: str,
    mfa_enabled: bool = False,
    issued_at: int | None = None,
) -> dict[str, str | bool | int]:
    """Build explicit Hub-user claims shared by every full-session issuer."""

    identity = local_user_tenant_id(username)
    issued = int(time.time()) if issued_at is None else int(issued_at)
    return {
        "sub": identity,
        "tenant_id": identity,
        "role": role,
        "mfa_enabled": mfa_enabled,
        "iat": issued,
        "exp": issued + settings.auth_access_token_ttl_seconds,
    }


def issue_user_access_token(
    *,
    username: str,
    role: str,
    mfa_enabled: bool = False,
) -> str:
    payload = build_user_access_token_claims(
        username=username,
        role=role,
        mfa_enabled=mfa_enabled,
    )
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def issue_user_session_tokens(
    *,
    username: str,
    role: str,
    mfa_enabled: bool = False,
    persist_refresh_token: bool = True,
) -> dict[str, str | bool]:
    canonical_username = local_user_tenant_id(username)
    access_token = issue_user_access_token(
        username=canonical_username,
        role=role,
        mfa_enabled=mfa_enabled,
    )

    refresh_token = ""
    if persist_refresh_token:
        repos = get_repository_registry()
        if repos.user_repo.get_by_username(canonical_username) is not None:
            try:
                refresh_token = secrets.token_urlsafe(64)
                repos.refresh_token_repo.save(
                    RefreshTokenDB(
                        token=refresh_token,
                        username=canonical_username,
                        expires_at=time.time() + settings.auth_refresh_token_ttl_seconds,
                    )
                )
            except IntegrityError:
                refresh_token = ""
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "username": canonical_username,
        "role": role,
        "mfa_required": mfa_enabled,
    }


__all__ = [
    "MAX_USER_SESSION_IDENTITY_LENGTH",
    "UserSessionIdentityError",
    "build_user_access_token_claims",
    "issue_user_access_token",
    "issue_user_session_tokens",
    "local_user_tenant_id",
]
