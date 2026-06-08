"""Capability model for simulation actions (SIM-034).

Each agent role has an allowed action set. Actions outside it are blocked
before reaching the PolicyEngine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_DEFAULT_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "citizen":  frozenset({"move", "eat", "rest", "give", "harvest",
                            "communicate", "vote", "noop", "work", "trade"}),
    "hunter":   frozenset({"move", "eat", "rest", "attack", "harvest",
                            "communicate", "vote", "noop", "work", "explore"}),
    "farmer":   frozenset({"move", "eat", "rest", "harvest", "give",
                            "communicate", "vote", "noop", "work", "trade"}),
    "merchant": frozenset({"move", "eat", "rest", "trade", "give", "take",
                            "communicate", "vote", "noop", "work"}),
    "medic":    frozenset({"move", "eat", "rest", "heal", "give",
                            "communicate", "vote", "noop", "work"}),
    "builder":  frozenset({"move", "eat", "rest", "build", "harvest", "give",
                            "communicate", "vote", "noop", "work"}),
    "explorer": frozenset({"move", "eat", "rest", "explore", "harvest",
                            "communicate", "vote", "noop", "attack"}),
    "ruler":    frozenset({"move", "eat", "rest", "attack", "give", "take",
                            "communicate", "vote", "propose_law", "noop", "work"}),
    "_any":     frozenset({"noop", "rest", "communicate", "vote"}),
}


@dataclass
class CapabilityCheck:
    allowed: bool
    reason: str


class AgentCapabilityModel:
    """Per-role capability enforcement for simulation actions."""

    def __init__(self, role_caps: dict[str, frozenset[str]] | None = None) -> None:
        self._caps = role_caps or _DEFAULT_ROLE_CAPABILITIES

    def is_allowed(self, role: str, action_type: str) -> CapabilityCheck:
        allowed_for_role = self._caps.get(role, self._caps["_any"])
        if action_type in allowed_for_role:
            return CapabilityCheck(allowed=True, reason="in_role_capabilities")
        # Always allow noop
        if action_type == "noop":
            return CapabilityCheck(allowed=True, reason="noop_always_allowed")
        return CapabilityCheck(
            allowed=False,
            reason=f"action {action_type!r} not in capabilities for role {role!r}",
        )

    def capabilities_for(self, role: str) -> frozenset[str]:
        return self._caps.get(role, self._caps["_any"])

    def register_role(self, role: str, actions: frozenset[str]) -> None:
        self._caps[role] = actions
