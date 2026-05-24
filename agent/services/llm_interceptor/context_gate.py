from __future__ import annotations

from typing import Any


class ContextGate:
    """Attach/filter context snippets based on upstream trust and policy decision."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = dict(cfg or {})

    def gate(
        self,
        *,
        snippets: list[dict[str, Any]],
        upstream_trust_level: str,
        decision: dict[str, Any],
        worker: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        _ = worker
        cloud = str(upstream_trust_level or "").lower() == "cloud"
        action = str((decision or {}).get("action") or "allow").lower()
        allowed: list[dict[str, Any]] = []
        denied = 0
        for s in list(snippets or []):
            item = dict(s)
            source = str(item.get("source_type") or item.get("source") or "").lower()
            sensitivity = str(item.get("sensitivity") or "internal").lower()
            if cloud and source in {"repo", "workspace", "code"}:
                denied += 1
                continue
            if cloud and sensitivity in {"internal_high", "secret", "credential", "security_sensitive"}:
                denied += 1
                continue
            if action == "reduce_context" and cloud:
                text = str(item.get("content") or "")
                item["content"] = text[:500]
            allowed.append(item)
        return allowed, {"input_count": len(list(snippets or [])), "allowed_count": len(allowed), "denied_count": denied}

