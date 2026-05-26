from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _jsonify(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonify(item) for item in value]
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class CodeTerritory:
    id: str
    name: str
    path: str
    module: str | None = None
    risk_level: str = "medium"
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodeTerritory":
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            path=str(payload["path"]),
            module=(None if payload.get("module") is None else str(payload["module"])),
            risk_level=str(payload.get("risk_level") or "medium"),
            tags=tuple(str(item) for item in payload.get("tags") or ()),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AgentUnit:
    id: str
    role: str
    capabilities: tuple[str, ...] = ()
    allowed_context: tuple[str, ...] = ()
    risk_level: str = "medium"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentUnit":
        return cls(
            id=str(payload["id"]),
            role=str(payload["role"]),
            capabilities=tuple(str(item) for item in payload.get("capabilities") or ()),
            allowed_context=tuple(str(item) for item in payload.get("allowed_context") or ()),
            risk_level=str(payload.get("risk_level") or "medium"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PolicyNode:
    id: str
    policy_type: str
    effect: str
    scope: tuple[str, ...] = ()
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolicyNode":
        return cls(
            id=str(payload["id"]),
            policy_type=str(payload["policy_type"]),
            effect=str(payload["effect"]),
            scope=tuple(str(item) for item in payload.get("scope") or ()),
            reason=(None if payload.get("reason") is None else str(payload["reason"])),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ContextGate:
    id: str
    territory_id: str
    visibility: str
    allowed_roles: tuple[str, ...] = ()
    local_only: bool = False
    secret: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextGate":
        return cls(
            id=str(payload["id"]),
            territory_id=str(payload["territory_id"]),
            visibility=str(payload.get("visibility") or "hidden"),
            allowed_roles=tuple(str(item) for item in payload.get("allowed_roles") or ()),
            local_only=bool(payload.get("local_only", False)),
            secret=bool(payload.get("secret", False)),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ArtifactObjective:
    id: str
    task_id: str
    artifact_kind: str
    verification_required: bool = True
    evidence_refs: tuple[str, ...] = ()
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArtifactObjective":
        return cls(
            id=str(payload["id"]),
            task_id=str(payload["task_id"]),
            artifact_kind=str(payload["artifact_kind"]),
            verification_required=bool(payload.get("verification_required", True)),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs") or ()),
            status=str(payload.get("status") or "open"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TrustEdge:
    id: str
    source_id: str
    target_id: str
    relationship: str
    direction: str = "directed"
    trust_value: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrustEdge":
        return cls(
            id=str(payload["id"]),
            source_id=str(payload["source_id"]),
            target_id=str(payload["target_id"]),
            relationship=str(payload["relationship"]),
            direction=str(payload.get("direction") or "directed"),
            trust_value=float(payload.get("trust_value") or 0.0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class GameMap:
    id: str
    title: str
    territories: tuple[CodeTerritory, ...] = ()
    agents: tuple[AgentUnit, ...] = ()
    policy_nodes: tuple[PolicyNode, ...] = ()
    context_gates: tuple[ContextGate, ...] = ()
    artifact_objectives: tuple[ArtifactObjective, ...] = ()
    trust_edges: tuple[TrustEdge, ...] = ()
    degraded: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "territories": [item.to_dict() for item in self.territories],
            "agents": [item.to_dict() for item in self.agents],
            "policy_nodes": [item.to_dict() for item in self.policy_nodes],
            "context_gates": [item.to_dict() for item in self.context_gates],
            "artifact_objectives": [item.to_dict() for item in self.artifact_objectives],
            "trust_edges": [item.to_dict() for item in self.trust_edges],
            "degraded": self.degraded,
            "metadata": _jsonify(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GameMap":
        return cls(
            id=str(payload["id"]),
            title=str(payload["title"]),
            territories=tuple(CodeTerritory.from_dict(item) for item in payload.get("territories") or ()),
            agents=tuple(AgentUnit.from_dict(item) for item in payload.get("agents") or ()),
            policy_nodes=tuple(PolicyNode.from_dict(item) for item in payload.get("policy_nodes") or ()),
            context_gates=tuple(ContextGate.from_dict(item) for item in payload.get("context_gates") or ()),
            artifact_objectives=tuple(
                ArtifactObjective.from_dict(item) for item in payload.get("artifact_objectives") or ()
            ),
            trust_edges=tuple(TrustEdge.from_dict(item) for item in payload.get("trust_edges") or ()),
            degraded=bool(payload.get("degraded", False)),
            metadata=dict(payload.get("metadata") or {}),
        )

    @classmethod
    def from_json(cls, payload: str) -> "GameMap":
        return cls.from_dict(json.loads(payload))
