"""Voting/Governance system (SIM-010)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from simulation.models.world_state import LawState, SimEvent, WorldState


VoteChoice = Literal["yes", "no", "abstain"]
ProposalStatus = Literal["open", "passed", "rejected", "expired"]


@dataclass
class VoteRecord:
    agent_id: str
    choice: VoteChoice
    reason: str = ""


@dataclass
class GovernanceProposal:
    id: str
    proposer_id: str
    tick_created: int
    description: str
    kind: Literal["new_law", "repeal_law", "pardon", "policy_change"]
    payload: dict[str, Any] = field(default_factory=dict)
    votes: list[VoteRecord] = field(default_factory=list)
    status: ProposalStatus = "open"
    tick_decided: int | None = None
    ttl: int = 5  # ticks before expiry

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "proposer_id": self.proposer_id,
            "tick_created": self.tick_created, "description": self.description,
            "kind": self.kind, "payload": self.payload,
            "votes": [{"agent_id": v.agent_id, "choice": v.choice, "reason": v.reason}
                       for v in self.votes],
            "status": self.status, "tick_decided": self.tick_decided, "ttl": self.ttl,
        }


class GovernanceSystem:
    """Simple majority-vote governance system for the simulation."""

    def __init__(self, quorum_fraction: float = 0.5) -> None:
        self.quorum_fraction = quorum_fraction
        self._proposals: dict[str, GovernanceProposal] = {}

    # ── proposal lifecycle ────────────────────────────────────────────────────

    def submit_proposal(self, state: WorldState, proposer_id: str,
                         kind: str, description: str,
                         payload: dict[str, Any] | None = None) -> GovernanceProposal:
        pid = f"prop-{uuid.uuid4().hex[:8]}"
        prop = GovernanceProposal(
            id=pid, proposer_id=proposer_id, tick_created=state.tick,
            description=description, kind=kind,  # type: ignore[arg-type]
            payload=payload or {},
        )
        self._proposals[pid] = prop
        state.apply_event(SimEvent(
            tick=state.tick, kind="governance", actor_id=proposer_id,
            description=f"proposal submitted: {description}",
            data={"proposal_id": pid, "kind": kind},
        ))
        return prop

    def cast_vote(self, state: WorldState, proposal_id: str,
                   agent_id: str, choice: VoteChoice, reason: str = "") -> None:
        prop = self._proposals.get(proposal_id)
        if not prop or prop.status != "open":
            return
        # Replace existing vote if any
        prop.votes = [v for v in prop.votes if v.agent_id != agent_id]
        prop.votes.append(VoteRecord(agent_id=agent_id, choice=choice, reason=reason))
        state.apply_event(SimEvent(
            tick=state.tick, kind="governance", actor_id=agent_id,
            description=f"{agent_id} voted {choice} on {proposal_id}",
            data={"proposal_id": proposal_id, "choice": choice},
        ))

    def tick(self, state: WorldState) -> list[SimEvent]:
        """Resolve mature proposals; expire old ones."""
        events: list[SimEvent] = []
        total_living = len(state.living_agents())
        for prop in list(self._proposals.values()):
            if prop.status != "open":
                continue
            age = state.tick - prop.tick_created
            yes = sum(1 for v in prop.votes if v.choice == "yes")
            no = sum(1 for v in prop.votes if v.choice == "no")
            quorum = max(1, int(total_living * self.quorum_fraction))
            voted = yes + no

            if voted >= quorum or age >= prop.ttl:
                if age >= prop.ttl and voted < quorum:
                    prop.status = "expired"
                    ev = self._make_event(state, prop, "expired")
                elif yes > no:
                    prop.status = "passed"
                    ev = self._make_event(state, prop, "passed")
                    self._apply_proposal(state, prop)
                else:
                    prop.status = "rejected"
                    ev = self._make_event(state, prop, "rejected")
                prop.tick_decided = state.tick
                state.apply_event(ev)
                events.append(ev)
        return events

    def open_proposals(self) -> list[GovernanceProposal]:
        return [p for p in self._proposals.values() if p.status == "open"]

    # ── internal ──────────────────────────────────────────────────────────────

    def _make_event(self, state: WorldState, prop: GovernanceProposal, outcome: str) -> SimEvent:
        return SimEvent(
            tick=state.tick, kind="governance", actor_id=prop.proposer_id,
            description=f"proposal {prop.id} {outcome}: {prop.description}",
            data={"proposal_id": prop.id, "outcome": outcome, **prop.as_dict()},
        )

    def _apply_proposal(self, state: WorldState, prop: GovernanceProposal) -> None:
        if prop.kind == "new_law":
            law = LawState(
                id=prop.payload.get("law_id", f"law-{prop.id}"),
                description=prop.payload.get("description", prop.description),
                forbidden_actions=prop.payload.get("forbidden_actions", []),
                penalty=prop.payload.get("penalty", "reputation_loss"),
                severity=float(prop.payload.get("severity", 0.5)),
            )
            state.apply_law_change(law)
        elif prop.kind == "repeal_law":
            law_id = prop.payload.get("law_id")
            if law_id and law_id in state.laws:
                state.laws[law_id].active = False
        elif prop.kind == "pardon":
            agent_id = prop.payload.get("agent_id")
            ag = state.agents.get(agent_id) if agent_id else None
            if ag and ag.shelter_status == "imprisoned":
                ag.shelter_status = "outdoors"
