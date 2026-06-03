from __future__ import annotations

import secrets
import time

import jwt

from agent.config import settings
from agent.db_models import RefreshTokenDB
from agent.services.repository_registry import get_repository_registry


def issue_user_session_tokens(
    *,
    username: str,
    role: str,
    mfa_enabled: bool = False,
) -> dict[str, str | bool]:
    payload = {
        "sub": username,
        "role": role,
        "mfa_enabled": mfa_enabled,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.auth_access_token_ttl_seconds,
    }
    access_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

    refresh_token = secrets.token_urlsafe(64)
    get_repository_registry().refresh_token_repo.save(
        RefreshTokenDB(
            token=refresh_token,
            username=username,
            expires_at=time.time() + settings.auth_refresh_token_ttl_seconds,
        )
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "username": username,
        "role": role,
        "mfa_required": mfa_enabled,
    }
