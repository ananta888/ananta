"""Governance modes for the AI-Snake heuristic system.

Governance controls learning, candidate creation, and activation.
It is orthogonal to movement_mode (follow_user, lurk, fast_target, …).
"""
from __future__ import annotations

from enum import Enum


class GovernanceMode(str, Enum):
    """Determines how new candidates are evaluated and activated."""

    AUTO_WITHOUT_HUMAN_APPROVAL = "auto_without_human_approval"
    """Full auto-activation after Validation + Simulation + Shadow. Default."""

    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    """Candidate waits for explicit operator approval before promotion."""

    OBSERVE_ONLY = "observe_only"
    """Snake observes TUI but creates no candidates and does no learning."""

    FROZEN = "frozen"
    """Active heuristic is locked; candidates accumulate but are never activated."""

    @classmethod
    def from_str(cls, value: str) -> "GovernanceMode":
        """Parse a governance mode string; raise ValueError on unknown value."""
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(m.value for m in cls)
            raise ValueError(
                f"Unknown governance_mode {value!r}. Valid values: {valid}"
            ) from None

    @property
    def allows_candidate_creation(self) -> bool:
        return self not in (GovernanceMode.OBSERVE_ONLY, GovernanceMode.FROZEN)

    @property
    def allows_auto_promotion(self) -> bool:
        return self == GovernanceMode.AUTO_WITHOUT_HUMAN_APPROVAL

    @property
    def requires_human_approval(self) -> bool:
        return self == GovernanceMode.HUMAN_APPROVAL_REQUIRED

