from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.sources.open_notebook_redaction import (
    contains_secret_value,
    looks_like_secret_key,
    redact_metadata_with_report,
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    sanitized_metadata: dict[str, Any] = field(default_factory=dict)
    redacted_fields: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "redacted_fields": self.redacted_fields,
        }


@dataclass(frozen=True)
class OpenNotebookImportPolicy:
    """Decides which parts of an OpenNotebook export may be imported.

    Defaults: sources, notes and insights are importable; chat sessions are
    not imported unless explicitly enabled. User-supplied content stays
    local_only unless the export marks it as approved for sharing.
    """

    allow_sources: bool = True
    allow_notes: bool = True
    allow_insights: bool = True
    allow_chat_sessions: bool = False

    _SECTION_FLAGS = {
        "sources": "allow_sources",
        "notes": "allow_notes",
        "source_insights": "allow_insights",
        "chat_sessions": "allow_chat_sessions",
    }

    def evaluate_section(self, section: str) -> PolicyDecision:
        flag_name = self._SECTION_FLAGS.get(str(section or "").strip().lower())
        if flag_name is None:
            return PolicyDecision(allowed=False, reason_code="unknown_section")
        if not bool(getattr(self, flag_name)):
            return PolicyDecision(allowed=False, reason_code=f"{section}_import_disabled")
        return PolicyDecision(allowed=True, reason_code="allowed")

    def evaluate_record(self, record: dict[str, Any], *, section: str) -> PolicyDecision:
        section_decision = self.evaluate_section(section)
        if not section_decision.allowed:
            return section_decision

        payload = dict(record or {})
        metadata = dict(payload.get("metadata") or {})

        blocked_keys = [key for key in payload if looks_like_secret_key(str(key))]
        if blocked_keys:
            return PolicyDecision(allowed=False, reason_code="secret_like_field_blocked")
        for value in payload.values():
            if isinstance(value, str) and contains_secret_value(value):
                return PolicyDecision(allowed=False, reason_code="secret_like_value_blocked")

        sanitized_metadata, redacted_fields = redact_metadata_with_report(metadata)
        if not isinstance(sanitized_metadata, dict):
            sanitized_metadata = {}

        sharing_approved = bool(sanitized_metadata.get("sharing_approved", False))
        if not sharing_approved:
            sanitized_metadata["llm_scope"] = "local_only"

        return PolicyDecision(
            allowed=True,
            reason_code="allowed",
            sanitized_metadata=sanitized_metadata,
            redacted_fields=redacted_fields,
        )
