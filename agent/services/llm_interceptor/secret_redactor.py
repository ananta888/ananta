from __future__ import annotations

import re
from typing import Any


_DEFAULT_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{6,}['\"]?"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b[A-Za-z0-9_]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
]


class SecretRedactor:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        c = dict(cfg or {})
        custom = []
        for raw in list(c.get("patterns") or []):
            try:
                custom.append(re.compile(str(raw), re.IGNORECASE))
            except re.error:
                continue
        self._patterns = [*_DEFAULT_PATTERNS, *custom]

    def redact_text(self, text: str) -> tuple[str, dict[str, int]]:
        value = str(text or "")
        hits = 0
        for pat in self._patterns:
            value, n = pat.subn("[REDACTED]", value)
            hits += int(n)
        return value, {"redaction_hits": hits}

    def redact_messages(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        out: list[dict[str, Any]] = []
        total = 0
        for msg in list(messages or []):
            item = dict(msg)
            content = item.get("content")
            if isinstance(content, str):
                red, meta = self.redact_text(content)
                item["content"] = red
                total += int(meta.get("redaction_hits") or 0)
            out.append(item)
        return out, {"redaction_hits": total}

