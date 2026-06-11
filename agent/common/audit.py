import logging
import hashlib
import json
import re
from typing import Any

from flask import g, has_request_context, request
from sqlmodel import Session, select

from agent.common.redaction import DEFAULT_SENSITIVE_KEYS, VisibilityLevel, redact
from agent.db_models import AuditLogDB
from agent.services.hub_event_service import build_hub_event

# AWTCL-008: tool calling loop audit event constants
AUDIT_WORKER_TOOL_REQUESTED = "ananta_worker_tool_requested"
AUDIT_WORKER_TOOL_COMPLETED = "ananta_worker_tool_completed"
AUDIT_WORKER_TOOL_BLOCKED = "ananta_worker_tool_blocked"
AUDIT_WORKER_TOOL_APPROVAL_REQUIRED = "ananta_worker_tool_approval_required"
# AWWPI-008/017: workspace mutation audit event constants.
# ALWA-012: canonical names are workspace_*; the legacy
# ananta_worker_mutation_* names are kept as deprecated aliases that
# resolve to the same event value. There must be no second event name
# for the same audit row (Supersede-Vertrag).
AUDIT_WORKSPACE_MUTATION_EVALUATED = "workspace_mutation_evaluated"
AUDIT_WORKSPACE_MUTATION_BLOCKED = "workspace_mutation_blocked"
AUDIT_WORKSPACE_BASELINE_CREATED = "workspace_baseline_created"

# Deprecated aliases — emit the canonical value, kept for back-compat
# with dashboards / log queries that still filter on the old names.
AUDIT_WORKER_MUTATION_EVALUATED = AUDIT_WORKSPACE_MUTATION_EVALUATED
AUDIT_WORKER_MUTATION_BLOCKED = AUDIT_WORKSPACE_MUTATION_BLOCKED

# HDE-009: hub-direct execution audit event constants. The hub decides
# and dispatches (control plane); execution happens in the worker
# runtime, which is audited via AUDIT_WORKER_RUNTIME_DISPATCH (HDW-005).
AUDIT_HUB_DIRECT_CANDIDATE_DETECTED = "hub_direct_candidate_detected"
AUDIT_HUB_DIRECT_TOOL_REQUESTED = "hub_direct_tool_requested"
AUDIT_HUB_DIRECT_TOOL_COMPLETED = "hub_direct_tool_completed"
AUDIT_HUB_DIRECT_TOOL_BLOCKED = "hub_direct_tool_blocked"
AUDIT_HUB_DIRECT_APPROVAL_REQUIRED = "hub_direct_approval_required"
AUDIT_HUB_DIRECT_FALLBACK_TO_WORKER = "hub_direct_fallback_to_worker"
AUDIT_WORKER_RUNTIME_DISPATCH = "worker_runtime_dispatch"

# AFH-T020: Artifact-first audit event constants
AUDIT_WORKER_HANDOFF_CREATED = "worker_handoff_created"
AUDIT_ARTIFACT_MANIFEST_COLLECTED = "artifact_manifest_collected"
AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED = "artifact_manifest_synthesized"
AUDIT_ARTIFACT_COMPLETION_DECIDED = "artifact_completion_decided"
AUDIT_TASK_FINALIZED_FROM_ARTIFACTS = "task_finalized_from_artifacts"
AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED = "advisory_json_parse_failed_ignored"
AUDIT_ARTIFACT_RECONCILIATION_APPLIED = "artifact_reconciliation_applied"

# Logger für Audit-Events
audit_logger = logging.getLogger("audit")
SENSITIVE_FIELDS = tuple(sorted({key for keys in DEFAULT_SENSITIVE_KEYS.values() for key in keys}))
_FORBIDDEN_RAW_FIELDS = {
    "prompt",
    "raw_prompt",
    "raw_prompts",
    "messages",
    "raw_messages",
    "raw_response",
    "response_text",
}


def _engine():
    from agent.database import engine

    return engine


def _drop_forbidden_raw_fields(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized = str(key or "").strip().lower()
            if normalized in _FORBIDDEN_RAW_FIELDS:
                cleaned[key] = "***REDACTED_AUDIT_PAYLOAD***"
            else:
                cleaned[key] = _drop_forbidden_raw_fields(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_forbidden_raw_fields(item) for item in value]
    return value


def _sanitize_details(details: dict | None) -> dict:
    sanitized = redact(details or {}, VisibilityLevel.USER)
    sanitized = _drop_forbidden_raw_fields(sanitized)
    return sanitized if isinstance(sanitized, dict) else {}


_TOOL_AUDIT_EXCERPT_CHARS = 600


def audit_worker_tool_event(
    action: str,
    *,
    tool_name: str,
    policy_decision: str,
    risk_class: str,
    task_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    detail: str | None = None,
) -> None:
    """AWTCL-008: audit one ToolRequest/ToolResult of the worker tool loop.

    Long outputs are excerpted before they reach the audit log; raw
    prompts/outputs never land here (log_audit additionally redacts
    sensitive keys and forbidden raw fields).
    """
    excerpt = str(detail or "")
    if len(excerpt) > _TOOL_AUDIT_EXCERPT_CHARS:
        excerpt = excerpt[:_TOOL_AUDIT_EXCERPT_CHARS] + "…[truncated]"
    log_audit(
        action,
        {
            "task_id": task_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "policy_decision": policy_decision,
            "risk_class": risk_class,
            "status": status,
            "detail_excerpt": excerpt or None,
        },
    )


def audit_hub_direct_event(
    action: str,
    *,
    tool_name: str | None = None,
    policy_decision: str | None = None,
    risk_class: str | None = None,
    reason_code: str | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    status: str | None = None,
    detail: str | None = None,
    **extras: Any,
) -> None:
    """HDE-009: audit one hub-direct decision/dispatch.

    Payloads carry IDs, tool name, policy decision, risk and reason
    codes — never secrets, raw prompts or full tool outputs (long
    details are excerpted, log_audit redacts on top). Audit failures
    must never break execution.
    """
    excerpt = str(detail or "")
    if len(excerpt) > _TOOL_AUDIT_EXCERPT_CHARS:
        excerpt = excerpt[:_TOOL_AUDIT_EXCERPT_CHARS] + "…[truncated]"
    details: dict[str, Any] = {
        "task_id": task_id,
        "goal_id": goal_id,
        "tool_name": tool_name,
        "policy_decision": policy_decision,
        "risk_class": risk_class,
        "reason_code": reason_code,
        "status": status,
        "detail_excerpt": excerpt or None,
    }
    for key, value in extras.items():
        if str(key or "").lower() in _FORBIDDEN_RAW_FIELDS:
            continue
        details[key] = value
    details = {k: v for k, v in details.items() if v is not None}
    try:
        log_audit(action, details)
    except Exception:
        audit_logger.error("hub_direct_audit_failed", exc_info=True)


# ALWA-012: workspace-audit helper. Forbidden raw fields are dropped,
# changed_paths are sorted + truncated with a count, only paths /
# hashes / IDs / short summaries reach the audit row.
_WORKSPACE_AUDIT_PATH_LIMIT = 50
_WORKSPACE_AUDIT_FORBIDDEN = _FORBIDDEN_RAW_FIELDS | {
    "full_diff",
    "unified_diff",
    "file_content",
    "raw_content",
    "before",
    "after",
}


def _truncate_changed_paths(
    paths: list[str] | None,
) -> tuple[list[str], bool, int]:
    if not paths:
        return [], False, 0
    sorted_paths = sorted({str(p) for p in paths if str(p).strip()})
    total = len(sorted_paths)
    if total <= _WORKSPACE_AUDIT_PATH_LIMIT:
        return sorted_paths, False, total
    return sorted_paths[:_WORKSPACE_AUDIT_PATH_LIMIT], True, total


def audit_workspace_mutation_event(
    action: str,
    *,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    iteration_number: int | None = None,
    mutation_mode: str | None = None,
    changed_paths: list[str] | None = None,
    diff_hash: str | None = None,
    diff_artifact_id: str | None = None,
    policy_decision: str | None = None,
    violation_ids: list[str] | None = None,
    violation_summary: str | None = None,
    blocked_reason: str | None = None,
    tests_result_ref: str | None = None,
    baseline_id: str | None = None,
    baseline_hash: str | None = None,
    workspace_root_hash_or_id: str | None = None,
    materialized_paths_count: int | None = None,
    **extras: Any,
) -> None:
    """ALWA-012: emit one workspace-audit event with ALWA-DD-006 redaction.

    Forbidden raw fields (prompt, raw_messages, full_diff, file_content,
    ...) are dropped before the audit row is written. changed_paths are
    sorted and truncated with a count + truncated flag. Content stays
    out: only paths, hashes, IDs and short summaries reach the log.
    """
    paths, truncated, total = _truncate_changed_paths(changed_paths)
    details: dict[str, Any] = {
        "task_id": task_id,
        "goal_id": goal_id,
        "trace_id": trace_id,
        "iteration_number": iteration_number,
        "mutation_mode": mutation_mode,
        "changed_paths": paths,
        "changed_paths_count": total,
        "changed_paths_truncated": truncated,
        "diff_hash": diff_hash,
        "diff_artifact_id": diff_artifact_id,
        "policy_decision": policy_decision,
        "violation_ids": list(violation_ids or []),
        "violation_summary": violation_summary,
        "blocked_reason": blocked_reason,
        "tests_result_ref": tests_result_ref,
        "baseline_id": baseline_id,
        "baseline_hash": baseline_hash,
        "workspace_root_hash_or_id": workspace_root_hash_or_id,
        "materialized_paths_count": materialized_paths_count,
    }
    # Drop None values to keep the audit row compact.
    details = {k: v for k, v in details.items() if v is not None}
    # Allow callers to attach extra metadata, but never raw content.
    for key, value in extras.items():
        if str(key or "").lower() in _WORKSPACE_AUDIT_FORBIDDEN:
            continue
        details[key] = value
    log_audit(action, details)


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
    sanitized_details = _sanitize_details(details)
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
        with Session(_engine()) as session:
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
