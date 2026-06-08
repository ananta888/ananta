"""Security Boundary — simulated vs real tools (SIM-033)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Actions that must NEVER cause real side-effects outside the simulation sandbox.
_REAL_WORLD_RISK_ACTIONS = frozenset({
    "exec_shell", "write_file", "read_file", "http_request",
    "send_email", "send_message", "call_tool",
})


@dataclass
class BoundaryViolation:
    action_type: str
    agent_id: str
    reason: str


class SimulationSecurityBoundary:
    """Hard wall between simulation state and real-world tools.

    All actions are validated against an allowlist; any action that could
    cause real effects (filesystem, network, shell) is blocked unconditionally.
    """

    def __init__(self, allowed_actions: frozenset[str] | None = None) -> None:
        from simulation.models.action import KNOWN_ACTION_TYPES
        self._allowed = allowed_actions or KNOWN_ACTION_TYPES

    def check(self, action_type: str, agent_id: str,
               args: dict[str, Any] | None = None) -> BoundaryViolation | None:
        if action_type in _REAL_WORLD_RISK_ACTIONS:
            return BoundaryViolation(
                action_type=action_type, agent_id=agent_id,
                reason="real_world_action_blocked",
            )
        if action_type not in self._allowed:
            return BoundaryViolation(
                action_type=action_type, agent_id=agent_id,
                reason="action_not_in_simulation_allowlist",
            )
        return None

    def is_safe(self, action_type: str, agent_id: str,
                 args: dict[str, Any] | None = None) -> bool:
        return self.check(action_type, agent_id, args) is None
