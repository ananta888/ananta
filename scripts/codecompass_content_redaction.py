"""Syntax-preserving sensitive-value redaction for CodeCompass indexing."""

from __future__ import annotations

import re

_ASSIGNMENT = re.compile(
    r"(?im)^(?P<indent>\s*)(?P<quote>[\"']?)(?P<key>[a-z_][a-z0-9_.-]*)"
    r"(?P=quote)(?P<separator>\s*(?::(?!=)|(?<![=!<>])=(?!=))\s*)"
    r"(?P<value>[^\n(){}\[\]]+)$"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_values(content: str) -> tuple[str, bool]:
    redaction_count = 0

    def redact_assignment(match: re.Match[str]) -> str:
        nonlocal redaction_count
        key = match.group("key").lower()
        key_segments = {part for part in re.split(r"[_.-]+", key) if part}
        is_sensitive = (
            any(
                marker in key
                for marker in (
                    "password",
                    "passwd",
                    "api_key",
                    "api-key",
                    "private_key",
                    "private-key",
                    "secret",
                )
            )
            or "token" in key_segments
        )
        if not is_sensitive:
            return match.group(0)
        redaction_count += 1
        suffix = "," if match.group("value").rstrip().endswith(",") else ""
        prefix = (
            match.group("indent")
            + match.group("quote")
            + match.group("key")
            + match.group("quote")
            + match.group("separator")
        )
        return f'{prefix}"[REDACTED]"{suffix}'

    redacted = _ASSIGNMENT.sub(redact_assignment, content)
    redacted, private_count = _PRIVATE_KEY.subn("[REDACTED PRIVATE KEY]", redacted)
    return redacted, bool(redaction_count or private_count)
