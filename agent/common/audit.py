import logging
import hashlib
import json
import re

from flask import g, has_request_context, request
from sqlmodel import Session, select

from agent.common.redaction import redact, VisibilityLevel
from agent.database import engine
from agent.db_models import AuditLogDB
from agent.services.hub_event_service import build_hub_event

# Logger für Audit-Events
audit_logger = logging.getLogger("audit")


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

    # Extra Felder für strukturiertes Logging (JSON) - maskiert
    sanitized_details = redact(details or {})
    extra = {"extra_fields": {"audit": True, "user": username, "ip": ip, "action": action, "details": sanitized_details}}

    event_context = build_hub_event(
        channel="audit",
        event_type=action,
        actor=username,
        details={},
        task_id=sanitized_details.get("task_id"),
        goal_id=sanitized_details.get("goal_id"),
        trace_id=sanitized_details.get("trace_id"),
        plan_id=sanitized_details.get("plan_id"),
        verification_record_id=sanitized_details.get("verification_record_id"),
    )
    sanitized_details = {**sanitized_details, "_event": {k: v for k, v in event_context.items() if k != "details"}}
    audit_logger.info(msg, extra=extra)

    try:
        with Session(engine) as session:
            previous = session.exec(select(AuditLogDB).order_by(AuditLogDB.id.desc())).first()
            prev_hash = previous.record_hash if previous else None
            hash_payload = {
                "username": username,
                "ip": ip,
                "action": action,
                "details": sanitized_details,
                "prev_hash": prev_hash,
            }
            record_hash = hashlib.sha256(
                json.dumps(hash_payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
            ).hexdigest()
            log_entry = AuditLogDB(
                username=username,
                ip=ip,
                action=action,
                trace_id=sanitized_details.get("trace_id"),
                goal_id=sanitized_details.get("goal_id"),
                task_id=sanitized_details.get("task_id"),
                plan_id=sanitized_details.get("plan_id"),
                verification_record_id=sanitized_details.get("verification_record_id"),
                prev_hash=prev_hash,
                record_hash=record_hash,
                details=sanitized_details,
            )
            session.add(log_entry)
            session.commit()
    except Exception as e:
        audit_logger.error(f"Failed to save audit log to database: {e}")
