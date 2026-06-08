"""Crime/Law/Consequence model (SIM-009)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulation.models.world_state import AgentState, LawState, SimEvent, WorldState


@dataclass
class CrimeRecord:
    tick: int
    agent_id: str
    law_id: str
    action_type: str
    penalty_applied: str
    severity: float
    crime_id: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class CrimeLedger:
    """In-memory crime records per run."""

    def __init__(self) -> None:
        self._records: list[CrimeRecord] = []

    def record(self, rec: CrimeRecord) -> None:
        self._records.append(rec)

    def by_agent(self, agent_id: str) -> list[CrimeRecord]:
        return [r for r in self._records if r.agent_id == agent_id]

    def crime_score(self, agent_id: str) -> float:
        """Sum of severities of crimes committed."""
        return sum(r.severity for r in self.by_agent(agent_id))

    def all_records(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self._records]


class CrimeConsequenceSystem:
    """Applies law penalties post-validation (enriches PolicyEngine results)."""

    def __init__(self, ledger: CrimeLedger | None = None) -> None:
        self.ledger = ledger or CrimeLedger()

    def apply_penalty(self, state: WorldState, agent_id: str, law: LawState,
                       action_type: str, crime_id: str) -> SimEvent:
        ag = state.agents.get(agent_id)
        penalty = law.penalty
        severity = law.severity

        if ag:
            if penalty == "reputation_loss":
                ag.reputation = max(0.0, ag.reputation - severity * 0.2)
            elif penalty == "fine":
                ag.inventory["gold"] = max(0.0, ag.inventory.get("gold", 0.0) - severity * 5.0)
            elif penalty in ("imprisonment", "exile"):
                ag.shelter_status = "imprisoned"
            elif penalty == "death":
                ag.alive = False

        rec = CrimeRecord(
            tick=state.tick, agent_id=agent_id, law_id=law.id,
            action_type=action_type, penalty_applied=penalty,
            severity=severity, crime_id=crime_id,
        )
        self.ledger.record(rec)

        ev = SimEvent(
            tick=state.tick, kind="crime", actor_id=agent_id,
            description=f"{agent_id} broke law {law.id} ({action_type}) → {penalty}",
            data=rec.as_dict(),
        )
        state.apply_event(ev)
        return ev

    def pardon(self, state: WorldState, agent_id: str) -> None:
        ag = state.agents.get(agent_id)
        if ag and ag.shelter_status == "imprisoned":
            ag.shelter_status = "outdoors"
        state.apply_event(SimEvent(
            tick=state.tick, kind="governance", actor_id=agent_id,
            description=f"{agent_id} pardoned",
            data={"kind": "pardon"},
        ))
