"""SCTR-006: adaptive routing memory for safe SnakeChat read intents."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.snake_chat_security_policy import (
    SnakeChatSecurityPolicy,
    check_tool_dispatch_allowed,
)


_SAFE_AUTO_ROUTES = {"filesystem_read", "git_read", "todo_read"}


def normalize_routing_pattern(text: str) -> str:
    """Normalize a user message enough for exact safe-pattern matching."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return normalized.rstrip("?.!")


@dataclass
class RoutingMemoryEntry:
    pattern: str
    route: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.9
    hit_count: int = 0
    last_used_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "route": self.route,
            "tool_args": dict(self.tool_args),
            "confidence": self.confidence,
            "hit_count": self.hit_count,
            "last_used_at": self.last_used_at,
        }


class SnakeChatRoutingMemory:
    """In-memory map of safe exact patterns to deterministic read routes."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._entries: dict[str, RoutingMemoryEntry] = {}

    def learn(
        self,
        *,
        question: str,
        route: str,
        tool_args: dict[str, Any] | None = None,
        policy: SnakeChatSecurityPolicy | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        if route not in _SAFE_AUTO_ROUTES:
            return False
        effective_policy = policy or SnakeChatSecurityPolicy()
        allowed, _ = check_tool_dispatch_allowed(route, policy=effective_policy)
        if not allowed:
            return False
        pattern = normalize_routing_pattern(question)
        if not pattern:
            return False
        self._entries[pattern] = RoutingMemoryEntry(
            pattern=pattern,
            route=route,
            tool_args=dict(tool_args or {}),
        )
        return True

    def match(self, question: str) -> RoutingMemoryEntry | None:
        if not self.enabled:
            return None
        pattern = normalize_routing_pattern(question)
        entry = self._entries.get(pattern)
        if entry is None:
            return None
        entry.hit_count += 1
        entry.last_used_at = time.time()
        return entry

    def entries(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self._entries.values()]
