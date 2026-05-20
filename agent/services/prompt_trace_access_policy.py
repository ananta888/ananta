"""Raw Prompt Access Policy for PromptTrace. PTI-013."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RawAccessDecision:
    def __init__(self, allowed: bool, reason: str) -> None:
        self.allowed = allowed
        self.reason = reason


class PromptTraceAccessPolicy:
    """Decides whether a caller may access raw (un-redacted) prompt content."""

    def check_raw_access(
        self,
        *,
        is_admin: bool = False,
        is_local: bool = False,
        raw_available: bool = False,
    ) -> RawAccessDecision:
        try:
            from agent.config import settings
            store_raw = bool(getattr(settings, "prompt_trace_store_raw_prompts", False))
            allowed_modes = list(getattr(settings, "prompt_trace_allowed_raw_access_modes", []) or [])
        except Exception:
            store_raw = False
            allowed_modes = []

        if not raw_available:
            return RawAccessDecision(False, "raw_not_stored")

        if not store_raw:
            return RawAccessDecision(False, "store_raw_prompts_disabled")

        if is_local and ("local_admin_debug" in allowed_modes or "local" in allowed_modes):
            return RawAccessDecision(True, "local_admin_debug_allowed")

        if is_admin and "admin" in allowed_modes:
            return RawAccessDecision(True, "admin_allowed")

        return RawAccessDecision(False, "raw_access_denied_by_policy")

    def audit_raw_access(self, trace_id: str, accessor: str | None = None) -> None:
        try:
            from agent.common.audit import log_audit
            log_audit("prompt_trace_raw_access", extra={"trace_id": trace_id, "accessor": accessor or "unknown"})
        except Exception as exc:
            logger.debug("Could not log raw access audit: %s", exc)


_POLICY: PromptTraceAccessPolicy | None = None


def get_trace_access_policy() -> PromptTraceAccessPolicy:
    global _POLICY
    if _POLICY is None:
        _POLICY = PromptTraceAccessPolicy()
    return _POLICY
