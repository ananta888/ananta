import logging
from flask import g, request

# Logger für Audit-Events
audit_logger = logging.getLogger("audit")

def log_audit(action: str, details: dict = None):
    """
    Protokolliert eine administrative Aktion.
    """
    details = details or {}
    
    # Benutzername aus g.user (JWT Payload) extrahieren oder aus Details
    user_info = getattr(g, "user", {})
    username = user_info.get("sub") or user_info.get("username")
    
    if not username:
        username = details.get("username") or details.get("target_user") or "anonymous"
        
    if not username and getattr(g, "is_admin", False) and not user_info:
        username = "admin_via_token"
        
    ip = request.remote_addr
    
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
