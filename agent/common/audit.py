import logging
import re
from flask import g, request, has_request_context
from sqlmodel import Session
from agent.database import engine
from agent.db_models import AuditLogDB

# Logger für Audit-Events
audit_logger = logging.getLogger("audit")

# Sensitive Felder die maskiert werden sollen
SENSITIVE_FIELDS = {"password", "new_password", "old_password", "api_key", "token", "secret", "authorization"}


def _sanitize_details(details: dict) -> dict:
    """Entfernt oder maskiert sensitive Daten aus Audit-Log Details."""
    if not isinstance(details, dict):
        return details

    sanitized = {}
    for key, value in details.items():
        if key.lower() in SENSITIVE_FIELDS:
            sanitized[key] = "***REDACTED***"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_details(value)
        elif isinstance(value, list):
            sanitized[key] = [_sanitize_details(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, str):
            # Maskiere potenzielle Secrets in Strings (z.B. "password=xyz")
            sanitized_str = value
            for field in SENSITIVE_FIELDS:
                pattern = rf"({field}\s*[=:]\s*)[^\s,\)]+"
                sanitized_str = re.sub(pattern, r"\1***", sanitized_str, flags=re.IGNORECASE)
            sanitized[key] = sanitized_str
        else:
            sanitized[key] = value

    return sanitized


def log_audit(action: str, details: dict = None):
    """
    Protokolliert eine administrative Aktion.
    """
    details = details or {}

    # Benutzername aus g.user (JWT Payload) extrahieren oder aus Details
    username = "anonymous"
    ip = "internal"

    if has_request_context():
        user_info = getattr(g, "user", {})
        username = user_info.get("sub") or user_info.get("username")

        if not username:
            username = details.get("username") or details.get("target_user")

        if not username and getattr(g, "is_admin", False) and not user_info:
            username = "admin_via_token"

        ip = request.remote_addr or "unknown"

    if not username:
        username = "anonymous"

    # Nachricht für das Log
    msg = f"Action: {action} | User: {username} | IP: {ip}"

    # Extra Felder für strukturiertes Logging (JSON)
    extra = {"extra_fields": {"audit": True, "user": username, "ip": ip, "action": action, "details": details or {}}}

    audit_logger.info(msg, extra=extra)

    # In Datenbank speichern mit sanitisierten Details
    try:
        sanitized_details = _sanitize_details(details or {})
        with Session(engine) as session:
            log_entry = AuditLogDB(username=username, ip=ip, action=action, details=sanitized_details)
            session.add(log_entry)
            session.commit()
    except Exception as e:
        audit_logger.error(f"Failed to save audit log to database: {e}")
