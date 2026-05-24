from __future__ import annotations

import re

# Patterns that look like secrets — redacted in captured terminal output
_REDACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.~+/]+=*", re.IGNORECASE),
    re.compile(r"(token[=:\s]+)[A-Za-z0-9\-_.~+/]{16,}", re.IGNORECASE),
    re.compile(r"(password[=:\s]+)\S{6,}", re.IGNORECASE),
    re.compile(r"(secret[=:\s]+)\S{6,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key[=:\s]+)\S{8,}", re.IGNORECASE),
    re.compile(r"(ANANTA_PASSWORD[=:\s]+)\S+", re.IGNORECASE),
    re.compile(r"(ANANTA_TOKEN[=:\s]+)\S+", re.IGNORECASE),
    # AWS-style keys
    re.compile(r"(AKIA[A-Z0-9]{16})"),
    # private key blocks
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----", re.DOTALL),
]

_REDACTION_MARKER = "***REDACTED***"


def redact_secrets(text: str) -> str:
    result = text
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + _REDACTION_MARKER, result)
    return result
