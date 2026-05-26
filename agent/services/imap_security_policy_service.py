from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s]+"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^@\s]+@"),
)


def default_mail_security_policy() -> dict[str, Any]:
    return {
        "default_worker_access": "denied",
        "default_llm_access": "denied",
        "allow_cloud_worker_context": False,
        "body_load_requires_open_action": True,
        "allowed_scopes": ["metadata_only"],
    }


def mail_exposure_label(scope: str) -> str:
    value = str(scope or "").strip()
    mapping = {
        "metadata_only": "metadata only",
        "body_excerpt": "body excerpt",
        "full_body": "full body",
        "attachment_ref": "attachment reference",
    }
    return mapping.get(value, "unknown")


def decide_mail_release(*, policy: dict[str, Any] | None, requested_scope: str, target: str) -> dict[str, Any]:
    resolved_policy = default_mail_security_policy() | dict(policy or {})
    scope = str(requested_scope or "metadata_only").strip() or "metadata_only"
    allowed_scopes = [str(item) for item in list(resolved_policy.get("allowed_scopes") or []) if str(item).strip()]
    is_cloud_target = str(target or "").strip() == "cloud_worker"
    allowed = scope in allowed_scopes
    reason_code = "allowed"
    if not allowed:
        reason_code = "scope_not_allowed"
    if is_cloud_target and not bool(resolved_policy.get("allow_cloud_worker_context")):
        allowed = False
        reason_code = "cloud_context_denied"
    return {
        "allowed": bool(allowed),
        "requested_scope": scope,
        "target": str(target or "").strip(),
        "reason_code": reason_code,
        "scope_label": mail_exposure_label(scope),
    }


def redact_mail_content(text: str) -> dict[str, Any]:
    source = str(text or "")
    masked = source
    hits = 0
    for pattern in _SECRET_PATTERNS:
        candidate = pattern.sub("[REDACTED_SECRET]", masked)
        if candidate != masked:
            hits += 1
        masked = candidate
    return {
        "text": masked,
        "redaction_status": "redacted" if hits else "not_required",
        "reason_code": "secret_detected" if hits else "clean",
        "redaction_hits": hits,
    }
