from __future__ import annotations

import re
from dataclasses import dataclass

_ALLOWED_TERM = re.compile(r"^[\w][\w .:+#@/-]*$", re.UNICODE)
_SECRET_LIKE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|private[_-]?key|password|secret)\s*[:=]",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?:api[_-]?key|access[_-]?token|private[_-]?key|password|secret)\s*[:=]\s*[^\s,;]{1,200}",
    re.IGNORECASE,
)
_INJECTION_LIKE = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior)|system\s+prompt|developer\s+message|"
    r"execute\s+(?:this|the following)|call\s+(?:a\s+)?tool|exfiltrat)",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?|file)://\S+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SanitizedTerm:
    value: str
    accepted: bool
    reason_code: str


def sanitize_generated_term(value: str, *, maximum_length: int) -> SanitizedTerm:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return SanitizedTerm("", False, "empty_term")
    if len(normalized) > max(2, int(maximum_length)):
        return SanitizedTerm("", False, "term_too_long")
    if ".." in normalized or "\\" in normalized:
        return SanitizedTerm("", False, "path_traversal_like_term")
    lowered = normalized.lower()
    if "http://" in lowered or "https://" in lowered or "file://" in lowered:
        return SanitizedTerm("", False, "url_like_term")
    if _SECRET_LIKE.search(normalized):
        return SanitizedTerm("", False, "secret_like_term")
    if _INJECTION_LIKE.search(normalized):
        return SanitizedTerm("", False, "instruction_like_term")
    if _ALLOWED_TERM.fullmatch(normalized) is None:
        return SanitizedTerm("", False, "unsupported_characters")
    return SanitizedTerm(normalized, True, "accepted")


def redact_untrusted_text(value: str, *, maximum_length: int) -> str:
    text = str(value or "").replace("\x00", " ")[: max(0, int(maximum_length))]
    text = _SECRET_ASSIGNMENT.sub("[REDACTED]", text)
    return text


def sanitize_generated_summary(value: str, *, maximum_length: int) -> str:
    text = redact_untrusted_text(value, maximum_length=maximum_length)
    text = _URL.sub("[REDACTED_URL]", text)
    if _INJECTION_LIKE.search(text):
        return ""
    return text
