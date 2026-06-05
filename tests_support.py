from __future__ import annotations

import os

from sqlmodel import Session

from agent.auth import INVALID_TOKEN_WARN_LAST, generate_token
from agent.config import settings
from agent.database import engine
from agent.db_models import BannedIPDB, LoginAttemptDB, UserDB


def initial_admin_credentials() -> tuple[str, str]:
    username = str(getattr(settings, "initial_admin_user", "") or os.environ.get("INITIAL_ADMIN_USER") or "admin").strip() or "admin"
    password = (
        getattr(settings, "initial_admin_password", None)
        or os.environ.get("INITIAL_ADMIN_PASSWORD")
        or "admin"
    )
    return username, str(password)


def seed_user(*, username: str, password: str, role: str = "user") -> None:
    from werkzeug.security import generate_password_hash

    with Session(engine) as session:
        user = session.get(UserDB, username)
        if user is None:
            user = UserDB(username=username, password_hash=generate_password_hash(password), role=role)
        else:
            user.password_hash = generate_password_hash(password)
            user.role = role
        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_backup_codes = []
        user.failed_login_attempts = 0
        user.lockout_until = None
        session.add(user)
        session.commit()


def seed_admin_user() -> tuple[str, str]:
    username, password = initial_admin_credentials()
    seed_user(username=username, password=password, role="admin")
    return username, password


def login_token(client, *, username: str, password: str) -> str:
    response = client.post("/login", json={"username": username, "password": password})
    payload = response.get_json(silent=True) or {}
    token = ((payload.get("data") or {}).get("access_token") or "").strip()
    if token:
        return token
    role = "admin" if username == "admin" else "user"
    return generate_token({"sub": username, "role": role, "mfa_enabled": False}, settings.secret_key)


def admin_login_token(client) -> str:
    username, password = seed_admin_user()
    return login_token(client, username=username, password=password)


def reset_auth_state() -> None:
    INVALID_TOKEN_WARN_LAST.clear()
    try:
        from agent.repository import banned_ip_repo, login_attempt_repo

        login_attempt_repo.clear_all()
        banned_ip_repo.clear_all()
    except Exception:
        pass
    try:
        from agent.services.rate_limit_service import get_rate_limit_service

        get_rate_limit_service().clear_all()
    except Exception:
        pass
