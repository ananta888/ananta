"""ActionProposal JSON schema (SIM-005) + Base actions (SIM-006)."""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


# ── ActionProposal (SIM-005) ─────────────────────────────────────────────────

KNOWN_ACTION_TYPES = frozenset({
    "move", "eat", "rest", "attack", "trade", "give", "take",
    "build", "harvest", "communicate", "vote", "propose_law",
    "heal", "flee", "work", "explore", "noop",
})

ActionDecision = Literal["allowed", "denied", "invalid", "crime", "fatal", "noop"]


class ActionProposal(BaseModel):
    agent_id: str
    action_type: str
    target: Optional[str] = None      # agent_id, location_id, or resource name
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    confidence: float = 1.0

    @model_validator(mode="after")
    def _check_known(self) -> "ActionProposal":
        if self.action_type not in KNOWN_ACTION_TYPES:
            raise ValueError(f"unknown action_type: {self.action_type!r}")
        return self

    @classmethod
    def invalid_fallback(cls, agent_id: str, raw: Any) -> "ActionProposal":
        return cls(agent_id=agent_id, action_type="noop",
                   reason=f"invalid_proposal:{str(raw)[:80]}", confidence=0.0)


class ActionValidationResult(BaseModel):
    decision: ActionDecision
    reason: str = ""
    effects: list[dict[str, Any]] = Field(default_factory=list)
    crime_id: Optional[str] = None


# ── Action effect schemas (SIM-006) ──────────────────────────────────────────

class MoveArgs(BaseModel):
    destination_id: str

class EatArgs(BaseModel):
    resource: str = "food"
    amount: float = 1.0

class AttackArgs(BaseModel):
    target_id: str
    weapon: str = "fists"
    intensity: float = 0.3    # 0-1

class TradeArgs(BaseModel):
    target_id: str
    give_resource: str
    give_amount: float
    want_resource: str
    want_amount: float

class GiveArgs(BaseModel):
    target_id: str
    resource: str
    amount: float

class HarvestArgs(BaseModel):
    resource: str
    amount: float = 1.0

class BuildArgs(BaseModel):
    structure: str
    resource_cost: dict[str, float] = Field(default_factory=dict)

class CommunicateArgs(BaseModel):
    target_id: Optional[str] = None    # None = broadcast
    message: str = ""

class VoteArgs(BaseModel):
    proposal_id: str
    vote: Literal["yes", "no", "abstain"]
    reason: str = ""

class ProposeLawArgs(BaseModel):
    description: str
    forbidden_actions: list[str] = Field(default_factory=list)
    penalty: str = "reputation_loss"
    severity: float = 0.5

class HealArgs(BaseModel):
    target_id: str
    amount: float = 0.2

class WorkArgs(BaseModel):
    task: str
    location_id: Optional[str] = None


# ── Action precondition helpers ───────────────────────────────────────────────

def check_alive(agent: Any) -> bool:
    return bool(getattr(agent, "alive", True))

def check_has_resource(agent: Any, resource: str, amount: float) -> bool:
    return agent.inventory.get(resource, 0.0) >= amount

def check_location_has_resource(location: Any, resource: str, amount: float) -> bool:
    return location.resources.get(resource, 0.0) >= amount
