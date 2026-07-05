from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

REDACTED_PLACEHOLDER = "[REDACTED]"

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|password|secret|authorization|bearer|credential)",
    re.IGNORECASE,
)
# Mirrors RagService._redact_sensitive plus common provider key prefixes.
_SECRET_VALUE_PATTERN = re.compile(r"\b(sk|rk|pk|ghp|gho|xoxb|xoxp)-[A-Za-z0-9_-]{8,}\b")


def looks_like_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERN.search(str(key or "")))


def contains_secret_value(value: str) -> bool:
    return bool(_SECRET_VALUE_PATTERN.search(str(value or "")))


def redact_text(value: str) -> tuple[str, int]:
    redacted, count = _SECRET_VALUE_PATTERN.subn(REDACTED_PLACEHOLDER, str(value or ""))
    return redacted, count


def redact_metadata(value: Any) -> tuple[Any, int]:
    """Return a redacted copy of nested dict/list metadata and the redaction count.

    Secret-looking keys are masked entirely; secret-looking values are masked
    in-place. Secret values are never logged.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            if looks_like_secret_key(str(key)):
                redacted[str(key)] = REDACTED_PLACEHOLDER
                count += 1
                continue
            nested, nested_count = redact_metadata(item)
            redacted[str(key)] = nested
            count += nested_count
        return redacted, count
    if isinstance(value, list):
        items: list[Any] = []
        count = 0
        for item in value:
            nested, nested_count = redact_metadata(item)
            items.append(nested)
            count += nested_count
        return items, count
    if isinstance(value, str):
        return redact_text(value)
    return value, 0


def redact_metadata_with_report(value: Any, *, context: str = "open_notebook") -> tuple[Any, int]:
    redacted, count = redact_metadata(value)
    if count:
        logger.info("open_notebook redaction masked %d field(s)", count, extra={"context": context})
    return redacted, count
