from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*[^\s]+"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^@\s]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_FAILURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "pytest_failure": re.compile(r"(?i)(=+\s*FAILURES\s*=+|pytest|assertionerror)"),
    "npm_failure": re.compile(r"(?i)(npm ERR!|npm error)"),
    "type_error": re.compile(r"(?i)\b(typeerror|mypy|typing error)\b"),
    "import_error": re.compile(r"(?i)\b(importerror|modulenotfounderror|cannot import)\b"),
    "lint_error": re.compile(r"(?i)\b(lint|ruff|flake8|eslint)\b"),
    "timeout": re.compile(r"(?i)\b(timeout|timed out|deadline exceeded)\b"),
}


def _redact_line(line: str) -> str:
    masked = str(line)
    replaced = False
    for pattern in _SECRET_PATTERNS:
        next_masked = pattern.sub("[REDACTED_SECRET]", masked)
        if next_masked != masked:
            replaced = True
        masked = next_masked
    return masked if replaced else str(line)


def extract_failure_log_insights(log_text: str, *, max_lines: int = 200) -> dict[str, Any]:
    lines = [str(item) for item in str(log_text or "").splitlines()]
    redacted = [_redact_line(line) for line in lines]
    redaction_hits = sum(1 for original, masked in zip(lines, redacted, strict=False) if original != masked)
    truncated = len(redacted) > int(max_lines)
    excerpt = redacted[: int(max_lines)]
    matches: list[str] = []
    joined = "\n".join(excerpt)
    for reason_code, pattern in _FAILURE_PATTERNS.items():
        if pattern.search(joined):
            matches.append(reason_code)
    findings = matches or ["unknown_failure_pattern"]
    return {
        "excerpt_lines": excerpt,
        "line_count_total": len(lines),
        "line_count_excerpt": len(excerpt),
        "truncated": truncated,
        "truncation_notice": "excerpt_truncated" if truncated else "",
        "detected_patterns": findings,
        "redaction_hits": redaction_hits,
        "redaction_status": "redacted" if redaction_hits > 0 else "not_required",
    }
