"""Policy gate for workflow adapter tool/network/file access (LCG-011, LCG-012)."""
from __future__ import annotations

from typing import Any

_ALWAYS_BLOCKED_TOOLS = frozenset({
    "exec_shell", "write_file", "delete_file", "read_file_arbitrary",
    "http_request", "network_scan", "spawn_process",
})

_HIGH_RISK_TOOLS = frozenset({
    "patch", "push", "write", "delete", "network", "shell",
})


class WorkflowPolicyGate:
    """Stateful policy gate; accumulates decisions for audit export.

    Default is DENY. Tools must be explicitly listed in ``allowed_tools``
    to pass. An empty ``allowed_tools`` set means *nothing* is allowed —
    not *everything*, which would invert the secure default.
    """

    def __init__(
        self,
        *,
        external_calls_allowed: bool = False,
        allowed_tools: set[str] | None = None,
        human_required_actions: set[str] | None = None,
    ) -> None:
        self._external_calls_allowed = external_calls_allowed
        self._allowed_tools: set[str] = allowed_tools or set()
        self._human_required: set[str] = human_required_actions or set()
        self._log: list[dict[str, Any]] = []

    @property
    def human_required_actions(self) -> frozenset[str]:
        """Read-only view of actions that always require human approval."""
        return frozenset(self._human_required)

    def check_tool(self, tool: str) -> dict[str, Any]:
        # 1. Hard-deny list always wins.
        if tool in _ALWAYS_BLOCKED_TOOLS:
            decision = {"tool": tool, "allowed": False, "reason": "always_blocked"}
        # 2. Empty allowlist = nothing allowed (default-deny).
        elif not self._allowed_tools:
            decision = {"tool": tool, "allowed": False, "reason": "default_deny_empty_allowlist"}
        # 3. Otherwise tool must be in the allowlist.
        elif tool in self._allowed_tools:
            decision = {"tool": tool, "allowed": True, "reason": "allowlisted"}
        else:
            decision = {"tool": tool, "allowed": False, "reason": "not_in_allowlist"}
        self._log.append(decision)
        return decision

    def requires_human(self, action: str) -> bool:
        """Public accessor for the human-required predicate (DIP-friendly)."""
        return action in self._human_required or action in _HIGH_RISK_TOOLS

    def check_network(self, url: str) -> dict[str, Any]:
        if not self._external_calls_allowed:
            decision = {"resource": url, "allowed": False, "reason": "external_calls_blocked"}
        else:
            decision = {"resource": url, "allowed": True, "reason": "external_calls_allowed"}
        self._log.append(decision)
        return decision

    def decisions_log(self) -> list[dict[str, Any]]:
        return list(self._log)

    def reset(self) -> None:
        self._log.clear()
