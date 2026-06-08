"""WorldState — versioniert, deterministisch, immutable-apply (SIM-003)."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    id: str
    name: str
    role: str
    location_id: str
    alive: bool = True
    # Survival stats (SIM-008)
    health: float = 1.0       # 0-1
    hunger: float = 0.0       # 0-1 (1 = starving)
    energy: float = 1.0       # 0-1
    morale: float = 1.0       # 0-1
    shelter_status: str = "outdoors"   # outdoors | sheltered | imprisoned
    # Resources on person
    inventory: dict[str, float] = field(default_factory=dict)
    # Memory (SIM-014)
    short_term_memory: list[dict[str, Any]] = field(default_factory=list)
    long_term_summary: str = ""
    # Social
    reputation: float = 0.5   # 0-1
    profile_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role,
            "location_id": self.location_id, "alive": self.alive,
            "health": self.health, "hunger": self.hunger,
            "energy": self.energy, "morale": self.morale,
            "shelter_status": self.shelter_status,
            "inventory": dict(self.inventory),
            "reputation": self.reputation,
            "profile_id": self.profile_id,
            "short_term_memory": list(self.short_term_memory),
            "long_term_summary": self.long_term_summary,
            "metadata": dict(self.metadata),
        }


@dataclass
class LocationState:
    id: str
    name: str
    resources: dict[str, float] = field(default_factory=dict)
    resource_regen: dict[str, float] = field(default_factory=dict)
    occupants: list[str] = field(default_factory=list)   # agent ids
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name,
                "resources": dict(self.resources),
                "occupants": list(self.occupants),
                "metadata": dict(self.metadata)}


@dataclass
class SimEvent:
    tick: int
    kind: str           # action_executed | action_denied | crime | death | governance | system
    actor_id: str | None
    description: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"tick": self.tick, "kind": self.kind,
                "actor_id": self.actor_id,
                "description": self.description, "data": dict(self.data)}


@dataclass
class LawState:
    id: str
    description: str
    forbidden_actions: list[str] = field(default_factory=list)
    penalty: str = "reputation_loss"
    severity: float = 1.0
    active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description,
                "forbidden_actions": list(self.forbidden_actions),
                "penalty": self.penalty, "severity": self.severity, "active": self.active}


@dataclass
class InstitutionState:
    id: str
    name: str
    kind: str       # government | market | court | guild
    member_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "member_ids": list(self.member_ids), "metadata": dict(self.metadata)}


class WorldState:
    """Mutable world state with deterministic hash.  Mutations via apply_*() only."""

    def __init__(
        self,
        scenario_name: str = "",
        tick: int = 0,
        agents: dict[str, AgentState] | None = None,
        locations: dict[str, LocationState] | None = None,
        laws: dict[str, LawState] | None = None,
        institutions: dict[str, InstitutionState] | None = None,
        events: list[SimEvent] | None = None,
        relationships: "RelationshipGraph | None" = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.tick = tick
        self.agents: dict[str, AgentState] = agents or {}
        self.locations: dict[str, LocationState] = locations or {}
        self.laws: dict[str, LawState] = laws or {}
        self.institutions: dict[str, InstitutionState] = institutions or {}
        self.events: list[SimEvent] = events or []
        self.relationships: RelationshipGraph = relationships or RelationshipGraph()
        self.metadata: dict[str, Any] = metadata or {}

    # ── mutation API ──────────────────────────────────────────────────────────

    def apply_event(self, event: SimEvent) -> None:
        self.events.append(event)

    def apply_agent_update(self, agent_id: str, **kwargs: Any) -> None:
        if agent_id not in self.agents:
            raise KeyError(f"agent {agent_id} not found")
        for k, v in kwargs.items():
            setattr(self.agents[agent_id], k, v)

    def apply_resource_delta(self, location_id: str, resource: str, delta: float) -> None:
        loc = self.locations[location_id]
        current = loc.resources.get(resource, 0.0)
        loc.resources[resource] = max(0.0, current + delta)

    def apply_inventory_delta(self, agent_id: str, resource: str, delta: float) -> None:
        ag = self.agents[agent_id]
        current = ag.inventory.get(resource, 0.0)
        ag.inventory[resource] = max(0.0, current + delta)

    def apply_law_change(self, law: LawState) -> None:
        self.laws[law.id] = law

    def advance_tick(self) -> None:
        self.tick += 1

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "tick": self.tick,
            "agents": {k: v.as_dict() for k, v in self.agents.items()},
            "locations": {k: v.as_dict() for k, v in self.locations.items()},
            "laws": {k: v.as_dict() for k, v in self.laws.items()},
            "institutions": {k: v.as_dict() for k, v in self.institutions.items()},
            "events": [e.as_dict() for e in self.events],
            "relationships": self.relationships.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorldState":
        ws = cls(scenario_name=d.get("scenario_name", ""), tick=d.get("tick", 0))
        for aid, ad in (d.get("agents") or {}).items():
            ws.agents[aid] = AgentState(**{k: v for k, v in ad.items()})
        for lid, ld in (d.get("locations") or {}).items():
            ws.locations[lid] = LocationState(**{k: v for k, v in ld.items()})
        for lid, ld in (d.get("laws") or {}).items():
            ws.laws[lid] = LawState(**{k: v for k, v in ld.items()})
        ws.relationships = RelationshipGraph.from_dict(d.get("relationships") or {})
        ws.events = [SimEvent(**e) for e in (d.get("events") or [])]
        ws.metadata = d.get("metadata") or {}
        return ws

    def state_hash(self) -> str:
        """Deterministic hash of the world state (excludes events for perf)."""
        payload = {
            "tick": self.tick,
            "agents": {k: v.as_dict() for k, v in sorted(self.agents.items())},
            "locations": {k: v.as_dict() for k, v in sorted(self.locations.items())},
            "laws": {k: v.as_dict() for k, v in sorted(self.laws.items())},
        }
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def snapshot(self) -> "WorldState":
        """Deep-copy for checkpointing."""
        return WorldState.from_dict(self.to_dict())

    def living_agents(self) -> list[AgentState]:
        return [a for a in self.agents.values() if a.alive]

    def agents_at(self, location_id: str) -> list[AgentState]:
        return [a for a in self.living_agents() if a.location_id == location_id]


# ── RelationshipGraph (SIM-011) ───────────────────────────────────────────────

@dataclass
class Relationship:
    source_id: str
    target_id: str
    trust: float = 0.0       # -1 to 1
    fear: float = 0.0        # 0 to 1
    friendship: float = 0.0  # -1 to 1
    hostility: float = 0.0   # 0 to 1
    obligation: float = 0.0  # 0 to 1  (debt/favor)

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "target_id": self.target_id,
                "trust": self.trust, "fear": self.fear,
                "friendship": self.friendship, "hostility": self.hostility,
                "obligation": self.obligation}


class RelationshipGraph:
    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], Relationship] = {}

    def get(self, a: str, b: str) -> Relationship:
        key = (a, b)
        if key not in self._edges:
            self._edges[key] = Relationship(source_id=a, target_id=b)
        return self._edges[key]

    def update(self, a: str, b: str, **kwargs: float) -> None:
        rel = self.get(a, b)
        for k, v in kwargs.items():
            current = getattr(rel, k, 0.0)
            setattr(rel, k, max(-1.0, min(1.0, current + v)))

    def visible_to(self, agent_id: str, max_hops: int = 1) -> list[Relationship]:
        """Return relationships involving agent_id (local view)."""
        return [r for r in self._edges.values()
                if r.source_id == agent_id or r.target_id == agent_id]

    def to_dict(self) -> dict[str, Any]:
        return {"edges": [r.as_dict() for r in self._edges.values()]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelationshipGraph":
        g = cls()
        for e in (d.get("edges") or []):
            g._edges[(e["source_id"], e["target_id"])] = Relationship(**e)
        return g
