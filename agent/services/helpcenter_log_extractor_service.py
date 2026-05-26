from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*[^\s]+"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
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
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[REDACTED_SECRET]", masked)
    return masked


def extract_failure_log_insights(log_text: str, *, max_lines: int = 200) -> dict[str, Any]:
    lines = [str(item) for item in str(log_text or "").splitlines()]
    redacted = [_redact_line(line) for line in lines]
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
    }
