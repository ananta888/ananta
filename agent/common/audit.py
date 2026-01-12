import logging
import time
from flask import g, request, has_request_context
from sqlmodel import Session
from agent.database import engine
from agent.db_models import AuditLogDB

# Logger für Audit-Events
audit_logger = logging.getLogger("audit")

def log_audit(action: str, details: dict = None):
    """
    Protokolliert eine administrative Aktion.
    """
    details = details or {}
    
    # Benutzername aus g.user (JWT Payload) extrahieren oder aus Details
    username = "anonymous"
    ip = "0.0.0.0"

    if has_request_context():
        user_info = getattr(g, "user", {})
        username = user_info.get("sub") or user_info.get("username")
        
        if not username:
            username = details.get("username") or details.get("target_user")
            
        if not username and getattr(g, "is_admin", False) and not user_info:
            username = "admin_via_token"
            
        ip = request.remote_addr
    
    if not username:
        username = "anonymous"

    # Nachricht für das Log
    msg = f"Action: {action} | User: {username} | IP: {ip}"
    
    # Extra Felder für strukturiertes Logging (JSON)
    extra = {
        "extra_fields": {
            "audit": True,
            "user": username,
            "ip": ip,
            "action": action,
            "details": details or {}
        }
    }
    
    audit_logger.info(msg, extra=extra)

    # In Datenbank speichern
    try:
        with Session(engine) as session:
            log_entry = AuditLogDB(
                username=username,
                ip=ip,
                action=action,
                details=details or {}
            )
            session.add(log_entry)
            session.commit()
    except Exception as e:
        audit_logger.error(f"Failed to save audit log to database: {e}")
