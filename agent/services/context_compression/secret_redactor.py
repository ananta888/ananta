"""
HCCA-006 — Secret Redactor

Regex-based secret detection and redaction. Zero external dependencies.
"""
from __future__ import annotations

import logging
import re
from enum import Enum

log = logging.getLogger(__name__)


class SensitivityLabel(str, Enum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    SECRET = "secret"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Pattern registry — (human-readable name, compiled regex)
# ---------------------------------------------------------------------------

_RAW_PATTERNS: list[tuple[str, str]] = [
    # OpenAI / Anthropic API keys
    ("OPENAI_API_KEY",     r"sk-[A-Za-z0-9]{32,}"),
    ("ANTHROPIC_API_KEY",  r"sk-ant-[A-Za-z0-9\-_]{32,}"),
    # Generic Bearer tokens
    ("BEARER_TOKEN",       r"Bearer\s+[A-Za-z0-9\-_\.=]{20,}"),
    # Password in URL query / config (password=VALUE or password: VALUE)
    ("PASSWORD_FIELD",     r"(?i)password\s*[:=]\s*[^\s\"'&]{6,}"),
    # PEM private keys
    ("PEM_PRIVATE_KEY",    r"-----BEGIN\s[\w\s]+PRIVATE KEY-----"),
    # Basic-auth URL: https://user:pass@host
    ("BASIC_AUTH_URL",     r"https?://[^:@\s]+:[^@\s]+@"),
    # AWS access key
    ("AWS_ACCESS_KEY",     r"AKIA[0-9A-Z]{16}"),
    # Generic high-entropy secret: known key names followed by 32+ non-space chars
    ("HIGH_ENTROPY_VALUE", r"(?i)(?:api_?key|auth_?token|secret|access_?token|private_?key)\s*[:=]\s*[A-Za-z0-9\-_/+\.]{32,}"),
    # GitHub personal access token (classic and fine-grained)
    ("GITHUB_TOKEN",       r"ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82,}"),
    # Slack bot/app tokens
    ("SLACK_TOKEN",        r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    # Google API keys
    ("GOOGLE_API_KEY",     r"AIza[0-9A-Za-z\-_]{35}"),
]

_SENSITIVE_NAMES: frozenset[str] = frozenset({
    "PASSWORD_FIELD",
    "BEARER_TOKEN",
    "HIGH_ENTROPY_VALUE",
})


class SecretRedactor:
    """Detect and redact secrets from text using compiled regex patterns."""

    SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        (name, re.compile(pattern))
        for name, pattern in _RAW_PATTERNS
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, text: str) -> SensitivityLabel:
        """Return the highest SensitivityLabel present in *text*."""
        matched_names: list[str] = []
        for name, pattern in self.SECRET_PATTERNS:
            if pattern.search(text):
                matched_names.append(name)

        if not matched_names:
            return SensitivityLabel.SAFE

        # Anything that is not purely "sensitive" level triggers SECRET
        sensitive_only = all(n in _SENSITIVE_NAMES for n in matched_names)
        if sensitive_only:
            return SensitivityLabel.SENSITIVE
        return SensitivityLabel.SECRET

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Replace matched secrets with [REDACTED:<name>] placeholders.

        Returns ``(redacted_text, list_of_redaction_reasons)``.
        """
        reasons: list[str] = []
        result = text
        for name, pattern in self.SECRET_PATTERNS:
            new_result, count = pattern.subn(f"[REDACTED:{name}]", result)
            if count:
                reasons.append(f"{name} ({count} occurrence{'s' if count > 1 else ''})")
                result = new_result
        return result, reasons

    def is_safe_to_store(self, text: str) -> bool:
        """Return True only if scan() returns SAFE."""
        return self.scan(text) == SensitivityLabel.SAFE
