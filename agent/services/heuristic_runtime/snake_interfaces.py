"""Snake system interfaces and data models (ASH-001 Phase A).

Defines:
  - MovementMode: movement strategies orthogonal to governance
  - SnakeRuntimeState: validated state container for one tick
  - CandidateRecord: schema-validated candidate data model
  - ActivationPolicy: configures when/how candidates are promoted

These are pure data models and protocols. No behavior is wired here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent.services.heuristic_runtime.governance import GovernanceMode
from agent.services.heuristic_runtime.snake_state_catalog import SnakeState


# ── Movement modes ────────────────────────────────────────────────────────────

class MovementMode(str):
    """Valid movement mode identifiers.

    Movement mode controls *how* the snake moves.
    Governance mode (GovernanceMode) controls *what it learns and activates*.
    """
    FOLLOW_USER  = "follow_user"
    LURK         = "lurk"
    FAST_TARGET  = "fast_target"
    KEYBOARD     = "keyboard"
    MOUSE_FOLLOW = "mouse_follow"
    IDLE         = "idle"

    _ALL = frozenset({
        "follow_user", "lurk", "fast_target", "keyboard", "mouse_follow", "idle"
    })

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


# ── Runtime state ─────────────────────────────────────────────────────────────

@dataclass
class SnakeRuntimeState:
    """Snapshot of AI-snake state for one tick.

    movement_mode and governance_mode are independent fields.
    Changing movement_mode never affects governance_mode and vice versa.
    """
    snake_state: SnakeState = SnakeState.HEURISTIC_FALLBACK
    movement_mode: str = MovementMode.FOLLOW_USER
    governance_mode: GovernanceMode = GovernanceMode.AUTO_WITHOUT_HUMAN_APPROVAL
    active_heuristic_id: str = ""
    current_candidate_id: str | None = None
    activation_strategy: str = "promote_to_active"
    last_reason_codes: list[str] = field(default_factory=list)
    rollout_stage: str | None = None   # "canary", "partial", "full"
    rollout_quota: float = 0.0
    activation_score: float | None = None
    risk_score: float | None = None
    direct_candidate_runtime: bool = False  # debug-only

    def with_movement(self, mode: str) -> "SnakeRuntimeState":
        import dataclasses
        return dataclasses.replace(self, movement_mode=mode)

    def with_governance(self, mode: GovernanceMode) -> "SnakeRuntimeState":
        import dataclasses
        return dataclasses.replace(self, governance_mode=mode)


# ── Candidate record ──────────────────────────────────────────────────────────

@dataclass
class CandidateRecord:
    """Schema-validated representation of a heuristic candidate file."""
    proposal_id: str
    domain: str
    base_heuristic_ref: str | None
    action_kind: str
    status: str                         # candidate | pending_simulation | auto_promoted | failed
    simulation_result: dict | None
    fingerprint: str = ""               # SHA-256 over (domain, base_heuristic_ref, action_kind, params_json)
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None     # created_at + TTL
    evidence_count: int = 1
    metrics: dict[str, Any] = field(default_factory=dict)
    score: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def simulation_passed(self) -> bool:
        if not isinstance(self.simulation_result, dict):
            return False
        return bool(self.simulation_result.get("can_activate", False))


# ── Activation policy ─────────────────────────────────────────────────────────

@dataclass
class ActivationPolicy:
    """Configures when and how candidates are promoted to active/."""
    candidate_activation_mode: GovernanceMode = GovernanceMode.AUTO_WITHOUT_HUMAN_APPROVAL
    requires_validation: bool = True
    requires_simulation: bool = True
    requires_shadow_run: bool = True
    requires_rollback_plan: bool = True
    progressive_rollout: bool = True
    rollout_quota_stages: list[float] = field(default_factory=lambda: [0.1, 0.5, 1.0])
    candidate_ttl_days: int = 14
    max_candidates_per_hour: int = 3
    direct_candidate_runtime_allowed: bool = False


# ── Service protocols ─────────────────────────────────────────────────────────

@runtime_checkable
class ISnakeHeuristicSelector(Protocol):
    """Selects the best matching active heuristic for the current context."""

    def select(
        self,
        heuristics: list[dict],
        *,
        section: str,
        ai_status: str,
        artifact_present: bool,
    ) -> tuple[dict | None, str]: ...
    """Returns (selected_heuristic, fallback_reason)."""


@runtime_checkable
class ISnakeCandidateService(Protocol):
    """Generates and persists heuristic candidates from decision traces."""

    def maybe_generate(self, traces: list, *, now: float) -> None: ...
    """Fire-and-forget: generate a candidate if thresholds are met."""


@runtime_checkable
class ISnakeActivationService(Protocol):
    """Promotes qualified candidates to active/ via shadow-run + progressive rollout."""

    def maybe_promote(self, candidate: CandidateRecord) -> bool: ...
    """Returns True if promotion was started."""
