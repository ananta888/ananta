"""Decide incremental update vs rebuild."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DecisionType(str, Enum):
    """Mögliche Entscheidungstypen der Engine."""

    NOOP = "noop"
    METADATA_ONLY = "metadata_only"
    DELTA_BUILD = "delta_build"
    PARTIAL_BASE_REBUILD = "partial_base_rebuild"
    ARTIFACT_KIND_REBASE = "artifact_kind_rebase"
    FULL_REBUILD = "full_rebuild"


@dataclass
class DecisionFactors:
    changeset_size: int
    direct_impact_count: int
    transitive_impact_count: int
    severity_score: float
    compatibility_broken: bool
    embedding_model_changed: bool
    fts_config_changed: bool
    graph_schema_changed: bool
    delta_depth: int
    max_delta_depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_size": self.changeset_size,
            "direct_impact_count": self.direct_impact_count,
            "transitive_impact_count": self.transitive_impact_count,
            "severity_score": round(self.severity_score, 4),
            "compatibility_broken": self.compatibility_broken,
            "embedding_model_changed": self.embedding_model_changed,
            "fts_config_changed": self.fts_config_changed,
            "graph_schema_changed": self.graph_schema_changed,
            "delta_depth": self.delta_depth,
            "max_delta_depth": self.max_delta_depth,
        }


@dataclass
class BuildDecision:
    decision_type: DecisionType
    confidence: float
    reason: str
    affected_artifact_kinds: list[str]
    estimated_cost: str
    dry_run_plan: dict[str, Any]
    factors: DecisionFactors

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type.value,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "affected_artifact_kinds": sorted(self.affected_artifact_kinds),
            "estimated_cost": self.estimated_cost,
            "dry_run_plan": self.dry_run_plan,
            "factors": self.factors.to_dict(),
        }


class IncrementalBuildDecisionEngine:
    SMALL_CHANGESET_THRESHOLD = 8
    MEDIUM_CHANGESET_THRESHOLD = 40
    LOW_SEVERITY_THRESHOLD = 0.2
    MEDIUM_SEVERITY_THRESHOLD = 0.45
    HIGH_SEVERITY_THRESHOLD = 0.75
    MAX_DELTA_DEPTH_DEFAULT = 8

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.small_changeset_threshold = int(cfg.get("small_changeset_threshold", self.SMALL_CHANGESET_THRESHOLD))
        self.medium_changeset_threshold = int(cfg.get("medium_changeset_threshold", self.MEDIUM_CHANGESET_THRESHOLD))
        self.low_severity_threshold = float(cfg.get("low_severity_threshold", self.LOW_SEVERITY_THRESHOLD))
        self.medium_severity_threshold = float(cfg.get("medium_severity_threshold", self.MEDIUM_SEVERITY_THRESHOLD))
        self.high_severity_threshold = float(cfg.get("high_severity_threshold", self.HIGH_SEVERITY_THRESHOLD))
        self.max_delta_depth = int(cfg.get("max_delta_depth", self.MAX_DELTA_DEPTH_DEFAULT))

    def _check_compatibility(self, old_profile: dict[str, Any] | None, new_profile: dict[str, Any] | None) -> bool:
        if not old_profile or not new_profile:
            return True
        return str(old_profile.get("profile_digest") or "") == str(new_profile.get("profile_digest") or "")

    def _check_embedding_model_change(self, old_profile: dict[str, Any] | None, new_profile: dict[str, Any] | None) -> bool:
        old = dict((old_profile or {}).get("embedding_profile") or {})
        new = dict((new_profile or {}).get("embedding_profile") or {})
        return (old.get("model"), old.get("dimensions"), old.get("embedding_text_profile")) != (
            new.get("model"),
            new.get("dimensions"),
            new.get("embedding_text_profile"),
        ) and bool(old or new)

    def _check_fts_config_change(self, old_profile: dict[str, Any] | None, new_profile: dict[str, Any] | None) -> bool:
        return dict((old_profile or {}).get("search_profile") or {}) != dict((new_profile or {}).get("search_profile") or {})

    def _check_graph_schema_change(self, old_profile: dict[str, Any] | None, new_profile: dict[str, Any] | None) -> bool:
        return dict((old_profile or {}).get("graph_profile") or {}) != dict((new_profile or {}).get("graph_profile") or {})

    def decide(
        self,
        changeset_size: int,
        impact_result: dict[str, Any] | None = None,
        build_profile_old: dict[str, Any] | None = None,
        build_profile_new: dict[str, Any] | None = None,
        delta_depth: int = 0,
    ) -> BuildDecision:
        impact = dict(impact_result or {})
        factors = DecisionFactors(
            changeset_size=int(changeset_size),
            direct_impact_count=len(list(impact.get("direct_impact") or [])),
            transitive_impact_count=len(list(impact.get("transitive_impact") or [])),
            severity_score=float(impact.get("severity_score") or 0.0),
            compatibility_broken=not self._check_compatibility(build_profile_old, build_profile_new),
            embedding_model_changed=self._check_embedding_model_change(build_profile_old, build_profile_new),
            fts_config_changed=self._check_fts_config_change(build_profile_old, build_profile_new),
            graph_schema_changed=self._check_graph_schema_change(build_profile_old, build_profile_new),
            delta_depth=int(delta_depth),
            max_delta_depth=self.max_delta_depth,
        )
        kinds = ["graph", "chunks", "embeddings", "fts"]
        if factors.changeset_size == 0 and not factors.compatibility_broken:
            decision = DecisionType.NOOP
            reason = "no_changes"
        elif factors.compatibility_broken or factors.delta_depth >= factors.max_delta_depth:
            decision = DecisionType.FULL_REBUILD
            reason = "compatibility_or_delta_depth"
        elif factors.embedding_model_changed:
            decision = DecisionType.ARTIFACT_KIND_REBASE
            reason = "embedding_model_changed"
            kinds = ["embeddings"]
        elif factors.graph_schema_changed:
            decision = DecisionType.ARTIFACT_KIND_REBASE
            reason = "graph_schema_changed"
            kinds = ["graph"]
        elif factors.fts_config_changed:
            decision = DecisionType.ARTIFACT_KIND_REBASE
            reason = "fts_config_changed"
            kinds = ["fts"]
        elif factors.changeset_size <= self.small_changeset_threshold and factors.severity_score < self.medium_severity_threshold:
            reasons = [item for item in list(impact.get("reason_codes") or []) if item]
            if reasons and all(item == "metadata_only" for item in reasons):
                decision = DecisionType.METADATA_ONLY
                reason = "metadata_only"
            else:
                decision = DecisionType.DELTA_BUILD
                reason = "small_compatible_change"
        elif factors.severity_score >= self.high_severity_threshold or factors.changeset_size > self.medium_changeset_threshold:
            decision = DecisionType.PARTIAL_BASE_REBUILD
            reason = "large_impact"
        else:
            decision = DecisionType.DELTA_BUILD
            reason = "local_impact"
        return BuildDecision(
            decision_type=decision,
            confidence=0.9 if decision in {DecisionType.NOOP, DecisionType.DELTA_BUILD} else 0.75,
            reason=reason,
            affected_artifact_kinds=kinds,
            estimated_cost=decision.value,
            dry_run_plan={"decision": decision.value, "kinds": kinds},
            factors=factors,
        )
