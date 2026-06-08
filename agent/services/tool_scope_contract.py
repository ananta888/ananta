"""ToolScopeContract (te-011) — normalizes allowed_tools / forbidden_tools.

Unifies the three places where tool allowlists appear:
  1. WorkerExecutionContextContract.allowed_tools
  2. WorkerJobContract.allowed_tools
  3. TaskWorkerContextSummaryContract.allowed_tools (in TaskScopedStepProposeResponse)

Provides a single `ToolScopeContract` that merges these sources and exposes
clean `is_allowed(tool)` / `is_forbidden(tool)` checks used by
`TaskEnginePolicyGate` and the strict_unknown_tool_policy path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.task_engine_policy_gate import KNOWN_TOOLS


@dataclass
class ToolScopeContract:
    """Normalized view of which tools a task execution context permits."""

    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    # "open" → anything not forbidden is allowed; "closed" → only explicit allowed list
    policy: str = "open"

    # ── factories ─────────────────────────────────────────────────────────────

    @classmethod
    def from_worker_context(cls, ctx: Any) -> "ToolScopeContract":
        """Build from a WorkerExecutionContextContract or dict."""
        if hasattr(ctx, "allowed_tools"):
            allowed = list(ctx.allowed_tools or [])
        elif isinstance(ctx, dict):
            allowed = list(ctx.get("allowed_tools") or [])
        else:
            allowed = []
        forbidden = list(getattr(ctx, "forbidden_tools", None) or ctx.get("forbidden_tools") if isinstance(ctx, dict) else [])
        policy = "closed" if allowed else "open"
        return cls(allowed_tools=_normalize(allowed), forbidden_tools=_normalize(forbidden), policy=policy)

    @classmethod
    def from_task(cls, task: dict[str, Any]) -> "ToolScopeContract":
        """Build from a raw task dict, merging all tool list fields."""
        allowed = _normalize(task.get("allowed_tools") or [])
        forbidden = _normalize(task.get("forbidden_tools") or [])
        # capabilities that imply specific tools
        caps = [str(c).strip().lower() for c in (task.get("required_capabilities") or [])]
        policy = "closed" if allowed else "open"
        return cls(allowed_tools=allowed, forbidden_tools=forbidden, policy=policy)

    @classmethod
    def open(cls) -> "ToolScopeContract":
        """Fully open scope — all known tools permitted."""
        return cls(policy="open")

    # ── checks ────────────────────────────────────────────────────────────────

    def is_allowed(self, tool: str) -> bool:
        t = tool.strip().lower()
        if t in self.forbidden_tools:
            return False
        if self.policy == "closed":
            return t in self.allowed_tools
        return True  # open policy: anything not forbidden is allowed

    def is_forbidden(self, tool: str) -> bool:
        return not self.is_allowed(tool)

    def unknown_tools_in(self, tool_calls: list[Any]) -> list[str]:
        """Return tool names that are not in KNOWN_TOOLS AND not in allowed_tools."""
        result: list[str] = []
        for tc in tool_calls:
            name = _extract_tool_name(tc)
            if name and name not in KNOWN_TOOLS and name not in self.allowed_tools:
                result.append(name)
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": self.allowed_tools,
            "forbidden_tools": self.forbidden_tools,
            "policy": self.policy,
        }

    def merge(self, other: "ToolScopeContract") -> "ToolScopeContract":
        """Merge two scopes — intersection of allowed, union of forbidden."""
        if self.policy == "open" and other.policy == "open":
            merged_allowed: list[str] = []
            policy = "open"
        elif self.policy == "closed" and other.policy == "closed":
            merged_allowed = [t for t in self.allowed_tools if t in other.allowed_tools]
            policy = "closed"
        else:
            # one open, one closed → use the closed one
            closed = self if self.policy == "closed" else other
            merged_allowed = list(closed.allowed_tools)
            policy = "closed"
        merged_forbidden = list(set(self.forbidden_tools) | set(other.forbidden_tools))
        return ToolScopeContract(allowed_tools=merged_allowed, forbidden_tools=merged_forbidden, policy=policy)


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize(tools: list[Any]) -> list[str]:
    return [str(t).strip().lower() for t in tools if str(t).strip()]


def _extract_tool_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return (tc.get("name") or tc.get("tool") or tc.get("function", {}).get("name") or "").lower()
    if isinstance(tc, str):
        return tc.lower()
    return ""
