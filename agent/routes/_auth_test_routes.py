"""SPLIT-037: Test-only auth helper routes (gated by auth_test_endpoints_enabled).

Mounted onto the shared ``auth_bp`` blueprint via :func:`register_routes`.
Behavior preserved 1:1 from the original auth.py (lines 894-1010).
"""
from __future__ import annotations

from flask import request
from werkzeug.security import generate_password_hash

from agent.auth import admin_required
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import UserDB
from agent.routes import auth as _auth_shim
from agent.routes._auth_helpers import _ensure_test_endpoint_enabled
from agent.routes._auth_password import validate_password_complexity

_repos = _auth_shim._repos


def register_routes(auth_bp) -> None:
    """Attach the test-only auth helper routes to the auth blueprint."""

    @auth_bp.route("/test/reset-login-attempts", methods=["POST"])
    @admin_required
    def test_reset_login_attempts():
        """
        Test-Helfer: Login-Attempts (und optional IP-Ban) für eine IP zurücksetzen.
        Nur aktiv, wenn AUTH_TEST_ENDPOINTS_ENABLED=1 gesetzt ist.
        """
        if not settings.auth_test_endpoints_enabled:
            return api_response(status="error", message="Not found", code=404)

        data = request.json or {}
        ip = data.get("ip") or request.remote_addr
        clear_ban = data.get("clear_ban", True)

        if not ip:
            return api_response(status="error", message="Missing ip", code=400)

        _repos().login_attempt_repo.delete_by_ip(ip)
        if clear_ban:
            _repos().banned_ip_repo.delete_by_ip(ip)

        return api_response(data={"status": "reset", "ip": ip, "ban_cleared": bool(clear_ban)})

    @auth_bp.route("/test/provision-user", methods=["POST"])
    def test_provision_user():
        err = _ensure_test_endpoint_enabled()
        if err:
            return err

        data = request.json or {}
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        role = str(data.get("role") or "user").strip().lower()
        overwrite = bool(data.get("overwrite", True))

        if not username or not password:
            return api_response(status="error", message="Missing username or password", code=400)
        if role not in {"admin", "user"}:
            return api_response(status="error", message="Invalid role", code=400)

        is_valid, error_msg = validate_password_complexity(password)
        if not is_valid:
            return api_response(status="error", message=error_msg, code=400)

        existing = _repos().user_repo.get_by_username(username)
        if existing and not overwrite:
            return api_response(status="error", message="User already exists", code=400)

        if existing:
            _repos().password_history_repo.delete_by_username(username)
            _repos().refresh_token_repo.delete_by_username(username)
            existing.password_hash = generate_password_hash(password)
            existing.role = role
            existing.mfa_secret = None
            existing.mfa_enabled = False
            existing.mfa_backup_codes = []
            existing.failed_login_attempts = 0
            existing.lockout_until = None
            _repos().user_repo.save(existing)
        else:
            _repos().user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role=role))

        return api_response(data={"status": "provisioned", "username": username, "role": role})

    @auth_bp.route("/test/users/<username>", methods=["DELETE"])
    def test_delete_user(username):
        err = _ensure_test_endpoint_enabled()
        if err:
            return err

        username = str(username or "").strip()
        if not username:
            return api_response(status="error", message="Missing username", code=400)
        if username == "admin":
            return api_response(status="error", message="Cannot delete main admin", code=400)

        _repos().password_history_repo.delete_by_username(username)
        _repos().refresh_token_repo.delete_by_username(username)
        deleted = _repos().user_repo.delete(username)
        if not deleted:
            return api_response(status="error", message="User not found", code=404)
        return api_response(data={"status": "deleted", "username": username})

    @auth_bp.route("/test/reset-user-auth-state", methods=["POST"])
    def test_reset_user_auth_state():
        err = _ensure_test_endpoint_enabled()
        if err:
            return err

        data = request.json or {}
        username = str(data.get("username") or "").strip()
        if not username:
            return api_response(status="error", message="Missing username", code=400)

        user = _repos().user_repo.get_by_username(username)
        if not user:
            return api_response(status="error", message="User not found", code=404)

        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_backup_codes = []
        user.failed_login_attempts = 0
        user.lockout_until = None

        new_password = data.get("password")
        if isinstance(new_password, str) and new_password:
            is_valid, error_msg = validate_password_complexity(new_password)
            if not is_valid:
                return api_response(status="error", message=error_msg, code=400)
            _repos().password_history_repo.delete_by_username(username)
            user.password_hash = generate_password_hash(new_password)

        _repos().user_repo.save(user)
        return api_response(data={"status": "reset", "username": username})
