"""SPLIT-037: Password complexity, rate limiting, history, lockout helpers.

Pure-ish service-layer helpers used by the auth route sub-modules.
No Flask route decorators live here — this module is the
single-responsibility home of every password/lockout policy check,
so that the route modules stay focused on HTTP plumbing.
"""
from __future__ import annotations

import re

from werkzeug.security import check_password_hash

from agent.common.audit import log_audit
from agent.config import settings


def validate_password_complexity(password):
    """
    Prüft, ob das Passwort die Komplexitätsanforderungen erfüllt:
    - Mindestens 12 Zeichen
    - Mindestens ein Großbuchstabe
    - Mindestens ein Kleinbuchstabe
    - Mindestens eine Zahl
    - Mindestens ein Sonderzeichen
    """
    if len(password) < settings.auth_password_min_length:
        return False, f"Password must be at least {settings.auth_password_min_length} characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    if not re.search(r"[ !@#$%^&*()_+\-=\-\{}\[\]:;\"'<>,.?/|\\~`]", password):
        return False, "Password must contain at least one special character."
    return True, ""


def is_rate_limited(ip):
    # Late import: agent.routes.auth is the canonical owner of these
    # helpers; looking them up at call time keeps tests that patch
    # ``agent.routes.auth._log`` working.
    from agent.routes import auth as _auth_shim

    # 1. Globalen IP-Ban prüfen
    if _auth_shim._repos().banned_ip_repo.is_banned(ip):
        return True

    # 2. Kurzfristiges Rate Limiting: 10 Versuche in 1 Minute
    count_1m = _auth_shim._repos().login_attempt_repo.get_recent_count(
        ip, window_seconds=settings.auth_rate_limit_window_short_seconds
    )
    if count_1m >= settings.auth_rate_limit_max_attempts_short:
        return True

    # 3. Langfristiges Rate Limiting (Fail2Ban-style): 50 Versuche in 1 Stunde -> 24h Sperre
    count_1h = _auth_shim._repos().login_attempt_repo.get_recent_count(
        ip, window_seconds=settings.auth_rate_limit_window_long_seconds
    )
    if count_1h >= settings.auth_rate_limit_max_attempts_long:
        _auth_shim._log().critical(
            f"IP {ip} banned for {settings.auth_ip_ban_duration_seconds}s due to "
            f"{settings.auth_rate_limit_max_attempts_long}+ failed attempts in "
            f"{settings.auth_rate_limit_window_long_seconds}s."
        )
        _auth_shim._repos().banned_ip_repo.ban_ip(
            ip,
            duration_seconds=settings.auth_ip_ban_duration_seconds,
            reason=(
                f"{settings.auth_rate_limit_max_attempts_long}+ failed attempts in "
                f"{settings.auth_rate_limit_window_long_seconds}s"
            ),
        )
        log_audit("ip_banned", {"ip": ip, "reason": "excessive_failed_logins"})
        return True

    return False


def check_password_history(username, new_password):
    """
    Prüft, ob das neue Passwort in den letzten 3 Passwörtern enthalten ist.
    """
    from agent.routes import auth as _auth_shim

    history = _auth_shim._repos().password_history_repo.get_by_username(
        username, limit=settings.auth_password_history_limit
    )
    for entry in history:
        if check_password_hash(entry.password_hash, new_password):
            return True
    return False


def record_attempt(ip):
    from agent.routes import auth as _auth_shim

    _auth_shim._repos().login_attempt_repo.record_attempt(ip)


def notify_lockout(username):
    """
    Simuliert eine Benachrichtigung bei Account-Sperrung.
    """
    from agent.routes import auth as _auth_shim

    _auth_shim._log().critical("ACCOUNT LOCKED: User %s has been locked out due to multiple failed attempts.", username)
    log_audit("account_lockout", {"username": username, "severity": "CRITICAL"})
    # Simulation E-Mail
    _auth_shim._log().info("Sending notification email to admin and user %s", username)
