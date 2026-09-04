"""Structured prompt construction and pre-persistence collaboration redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json

_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{12,}|(?:api[_-]?key|password|token|secret)\s*[:=]\s*[^\s,;]{6,})"
)


class CollaborationContentRedactor:
    """Removes secret-looking values before content reaches durable adapters."""

    SENSITIVE_KEYS = frozenset(
        {"authorization", "cookie", "password", "private_key", "secret", "token", "raw_tool_output"}
    )

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, nested in value.items():
                normalized = str(key).strip().casefold().replace("-", "_")
                if normalized in self.SENSITIVE_KEYS or normalized.endswith("_secret") or normalized.endswith("_token"):
                    result[str(key)] = "***REDACTED***"
                else:
                    result[str(key)] = self.redact(nested)
            return result
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return _SECRET_VALUE.sub("***REDACTED***", value)
        return value


class CollaborationPromptBuilder:
    """Keeps authority layers and untrusted data in distinct typed segments."""

    _MAX_SECTION_BYTES = 65_536

    def __init__(self, redactor: CollaborationContentRedactor | None = None) -> None:
        self._redactor = redactor or CollaborationContentRedactor()

    def build(
        self,
        *,
        runtime_rules: Mapping[str, Any],
        hub_policy: Mapping[str, Any],
        user_content: Sequence[Mapping[str, Any]],
        external_events: Sequence[Mapping[str, Any]],
        retrieval_sources: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        sections = [
            self._section("runtime_rules", "system_authority", runtime_rules),
            self._section("hub_policy", "hub_authority", hub_policy),
            self._section("user_content", "untrusted_data", list(user_content)),
            self._section("external_events", "untrusted_external_data", list(external_events)),
            self._section("retrieval_sources", "untrusted_retrieval_data", list(retrieval_sources)),
        ]
        return {
            "schema": "ananta.collaboration-prompt-bundle.v1",
            "sections": sections,
            "bundle_digest": canonical_digest(sections),
            "external_instructions_authoritative": False,
        }

    def grounded_security_claim(
        self,
        *,
        statement: str,
        source_refs: Sequence[str],
        run_refs: Sequence[str],
    ) -> dict[str, Any]:
        grounded = bool(statement.strip() and source_refs and len(run_refs) == 1)
        return {
            "statement": self._redactor.redact(statement)[:1024],
            "verification_status": "hub_verified" if grounded else "unverified",
            "source_refs": list(source_refs) if grounded else [],
            "run_refs": list(run_refs) if grounded else [],
        }

    def _section(self, name: str, trust: str, value: Any) -> dict[str, Any]:
        redacted = self._redactor.redact(value)
        if len(canonical_json(redacted).encode()) > self._MAX_SECTION_BYTES:
            raise ValueError(f"collaboration_prompt_section_too_large:{name}")
        return {"name": name, "trust": trust, "content": redacted}


__all__ = ["CollaborationContentRedactor", "CollaborationPromptBuilder"]
