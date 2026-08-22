"""Public Hub self-registration and administrator approval routes."""
from __future__ import annotations

import hashlib
import secrets
import time
from urllib.parse import urlencode

from flask import request
from werkzeug.security import generate_password_hash

from agent.auth import admin_required
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import UserDB
from agent.routes import auth as _auth_shim
from agent.routes._auth_password import validate_password_complexity
from agent.services.registration_email import get_registration_email_sender
from agent.services.user_session_tokens import UserSessionIdentityError, local_user_tenant_id


def _normalized_email(value: object) -> str:
    candidate = str(value or "").strip().lower()
    if "@" not in candidate or len(candidate) > 254:
        raise ValueError("invalid_email")
    local, domain = candidate.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid_email")
    return candidate


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_routes(auth_bp) -> None:
    @auth_bp.get("/registration/config")
    def registration_config():
        return api_response(data={
            "enabled": settings.hub_self_registration_enabled,
            "email_verification_required": True,
            "admin_approval_required": settings.hub_self_registration_require_admin_approval,
            "email_delivery_configured": bool(
                settings.hub_registration_smtp_host and settings.hub_registration_smtp_from
            ),
        })

    @auth_bp.post("/registrations")
    def create_registration():
        if not settings.hub_self_registration_enabled:
            return api_response(status="error", message="registration_disabled", code=404)
        data = request.get_json(silent=True) or {}
        try:
            username = local_user_tenant_id(data.get("username"))
            email = _normalized_email(data.get("email"))
        except (UserSessionIdentityError, ValueError, TypeError):
            return api_response(status="error", message="invalid_registration", code=400)
        password = str(data.get("password") or "")
        valid, message = validate_password_complexity(password)
        if not valid:
            return api_response(status="error", message=message, code=400)
        repos = _auth_shim._repos()
        if repos.user_repo.get_by_username(username) or repos.user_repo.get_by_email(email):
            return api_response(status="error", message="registration_conflict", code=409)
        token = secrets.token_urlsafe(32)
        now = time.time()
        user = UserDB(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role="user",
            registration_requires_admin=settings.hub_self_registration_require_admin_approval,
            admin_approved_at=None if settings.hub_self_registration_require_admin_approval else now,
            email_verification_token_hash=_token_hash(token),
            email_verification_expires_at=now + settings.hub_registration_verification_ttl_seconds,
        )
        repos.user_repo.save(user)
        base = settings.hub_registration_public_base_url.rstrip("/")
        url = f"{base}/verify-email?{urlencode({'token': token})}"
        try:
            get_registration_email_sender().send_verification(
                recipient=email, username=username, verification_url=url
            )
        except Exception:
            repos.user_repo.delete(username)
            _auth_shim._log().exception("Registration verification email delivery failed")
            return api_response(status="error", message="email_delivery_unavailable", code=503)
        log_audit("hub_registration_created", {"username": username, "requires_admin": user.registration_requires_admin})
        return api_response(data={"status": "verification_email_sent"}, code=201)

    @auth_bp.post("/registrations/verify-email")
    def verify_email():
        token = str((request.get_json(silent=True) or {}).get("token") or "")
        user = _auth_shim._repos().user_repo.get_by_verification_token_hash(_token_hash(token)) if token else None
        if not user or not user.email_verification_expires_at or user.email_verification_expires_at < time.time():
            return api_response(status="error", message="invalid_or_expired_token", code=400)
        user.email_verified_at = time.time()
        user.email_verification_token_hash = None
        user.email_verification_expires_at = None
        _auth_shim._repos().user_repo.save(user)
        status = "pending_admin_approval" if user.registration_requires_admin and not user.admin_approved_at else "active"
        log_audit("hub_registration_email_verified", {"username": user.username, "status": status})
        return api_response(data={"status": status})

    @auth_bp.get("/registrations/pending")
    @admin_required
    def pending_registrations():
        users = _auth_shim._repos().user_repo.get_pending_approval()
        return api_response(data=[{
            "username": user.username, "email": user.email,
            "email_verified": bool(user.email_verified_at),
        } for user in users])

    @auth_bp.post("/registrations/<username>/approve")
    @admin_required
    def approve_registration(username: str):
        user = _auth_shim._repos().user_repo.get_by_username(username)
        if not user or not user.email or not user.registration_requires_admin:
            return api_response(status="error", message="registration_not_found", code=404)
        user.admin_approved_at = time.time()
        _auth_shim._repos().user_repo.save(user)
        log_audit("hub_registration_admin_approved", {"username": username})
        return api_response(data={"status": "active" if user.email_verified_at else "pending_email_verification"})
