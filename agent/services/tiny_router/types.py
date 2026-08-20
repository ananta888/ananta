"""Versioned, side-effect-free contracts for tiny action-model routing."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

CAPABILITY_SCHEMA = "ananta.tiny_action_model_capability.v1"
ROUTING_DECISION_SCHEMA = "ananta.tiny_tool_routing_decision.v1"

STATUS_CANDIDATE = "candidate"
STATUS_SHADOW_CANDIDATE = "shadow_candidate"
STATUS_ABSTAIN = "abstain"
STATUS_ESCALATE = "escalate"
STATUS_DISABLED = "disabled"


@dataclass(frozen=True)
class TinyActionModelProfile:
    """Data-driven capability declaration, never an executable runtime."""

    profile_id: str
    model_id: str
    tier: str
    adapter: str
    dialect: str
    license_id: str
    source_url: str
    commercial_use_allowed: bool
    research_only: bool
    supports_confidence: bool
    supports_parallel_tools: bool
    max_tools: int
    context_window: int
    min_confidence: float
    local_only: bool = True
    enabled_by_default: bool = False
    artifact_sha256: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "TinyActionModelProfile":
        required = (
            "profile_id", "model_id", "tier", "adapter", "dialect",
            "license_id", "source_url",
        )
        missing = [key for key in required if not str(row.get(key) or "").strip()]
        if missing:
            raise ValueError("profile_fields_required:" + ",".join(sorted(missing)))
        confidence = float(row.get("min_confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("profile_min_confidence_out_of_range")
        max_tools = int(row.get("max_tools", 1))
        context_window = int(row.get("context_window", 1))
        if max_tools < 1 or context_window < 1:
            raise ValueError("profile_positive_limits_required")
        artifact_sha256 = str(row.get("artifact_sha256") or "").strip().lower()
        if artifact_sha256 and (
            len(artifact_sha256) != 64
            or any(char not in "0123456789abcdef" for char in artifact_sha256)
        ):
            raise ValueError("profile_artifact_sha256_invalid")
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("profile_metadata_must_be_object")
        return cls(
            profile_id=str(row["profile_id"]).strip(),
            model_id=str(row["model_id"]).strip(),
            tier=str(row["tier"]).strip(),
            adapter=str(row["adapter"]).strip(),
            dialect=str(row["dialect"]).strip(),
            license_id=str(row["license_id"]).strip(),
            source_url=str(row["source_url"]).strip(),
            commercial_use_allowed=bool(row.get("commercial_use_allowed", False)),
            research_only=bool(row.get("research_only", False)),
            supports_confidence=bool(row.get("supports_confidence", False)),
            supports_parallel_tools=bool(row.get("supports_parallel_tools", False)),
            max_tools=max_tools,
            context_window=context_window,
            min_confidence=confidence,
            local_only=bool(row.get("local_only", True)),
            enabled_by_default=bool(row.get("enabled_by_default", False)),
            artifact_sha256=artifact_sha256,
            metadata=dict(metadata),
        )

    def as_capability_dict(self) -> dict[str, Any]:
        return {
            "schema": CAPABILITY_SCHEMA,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "tier": self.tier,
            "adapter": self.adapter,
            "dialect": self.dialect,
            "license_id": self.license_id,
            "commercial_use_allowed": self.commercial_use_allowed,
            "research_only": self.research_only,
            "supports_confidence": self.supports_confidence,
            "supports_parallel_tools": self.supports_parallel_tools,
            "max_tools": self.max_tools,
            "context_window": self.context_window,
            "min_confidence": self.min_confidence,
            "local_only": self.local_only,
            "enabled_by_default": self.enabled_by_default,
            "artifact_sha256": self.artifact_sha256,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class ToolCallCandidate:
    tool_name: str
    arguments: Mapping[str, Any]
    confidence: float | None
    profile_id: str
    adapter_id: str

    def as_dict(self, *, include_arguments: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "tool_name": self.tool_name,
            "confidence": self.confidence,
            "profile_id": self.profile_id,
            "adapter_id": self.adapter_id,
        }
        if include_arguments:
            row["arguments"] = dict(self.arguments)
        return row


@dataclass(frozen=True)
class AdapterRequest:
    prompt: str
    tools: tuple[Mapping[str, Any], ...]
    profile: TinyActionModelProfile
    timeout_ms: int


@dataclass(frozen=True)
class AdapterResult:
    status: str
    payload: Any = None
    reason_code: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ValidationResult:
    status: str
    candidate: ToolCallCandidate | None = None
    reason_code: str = ""
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingAttempt:
    profile_id: str
    tier: str
    status: str
    reason_code: str
    latency_ms: float
    selected_tool_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "tier": self.tier,
            "status": self.status,
            "reason_code": self.reason_code,
            "latency_ms": round(self.latency_ms, 3),
            "selected_tool_count": self.selected_tool_count,
        }


@dataclass(frozen=True)
class RoutingDecision:
    status: str
    reason_code: str
    candidate: ToolCallCandidate | None = None
    attempts: tuple[RoutingAttempt, ...] = ()
    escalation_tier: str = "main"
    elapsed_ms: float = 0.0
    shadow: bool = False

    def as_dict(self, *, include_arguments: bool = False) -> dict[str, Any]:
        return {
            "schema": ROUTING_DECISION_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "candidate": (
                self.candidate.as_dict(include_arguments=include_arguments)
                if self.candidate else None
            ),
            "attempts": [item.as_dict() for item in self.attempts],
            "escalation_tier": self.escalation_tier,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "shadow": self.shadow,
        }


@dataclass(frozen=True)
class SchemaProjection:
    dialect: str
    tools: tuple[Mapping[str, Any], ...]
    losses: tuple[str, ...] = ()
