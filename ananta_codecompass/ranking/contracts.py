"""Versioned contracts for deterministic source ranking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Mapping, Protocol, Sequence

from .profiles import UNIVERSAL_SOURCE_WEIGHTS

RANKING_VERSION = "universal-source-ranking.v1"


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    signal: str
    raw_value: float
    normalized_value: float
    weight: float
    contribution: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    canonical_id: str
    path: str
    symbols: tuple[str, ...] = ()
    language: str = "unknown"
    content_excerpt: str = ""
    centrality: float | None = None
    graph_distance: int | None = None
    relation_evidence: tuple[str, ...] = ()
    entrypoint_evidence: tuple[str, ...] = ()
    eligible: bool = True
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RankingProfile:
    profile_id: str = "universal-default"
    version: str = RANKING_VERSION
    weights: Mapping[str, float] = field(default_factory=lambda: dict(UNIVERSAL_SOURCE_WEIGHTS))
    diversification_enabled: bool = True
    overrides_enabled: bool = False
    override_metadata: Mapping[str, str] = field(default_factory=dict)

    def digest(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RankingInput:
    query: str
    candidates: Sequence[RankingCandidate]
    repository_revision: str = "unknown"
    index_digest: str = "unknown"
    allowed_scope_digest: str = "unrestricted"
    profile: RankingProfile = field(default_factory=RankingProfile)
    model_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: RankingCandidate
    score: float
    confidence: float
    file_role: str
    role_confidence: float
    role_reasons: tuple[str, ...]
    contributions: tuple[ScoreContribution, ...]
    tie_breaker: str


@dataclass(frozen=True, slots=True)
class RankingResult:
    ranking_version: str
    profile_id: str
    profile_digest: str
    repository_revision: str
    index_digest: str
    model_digest: str | None
    partial_signals: tuple[str, ...]
    ranked: tuple[RankedCandidate, ...]

    def as_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), ensure_ascii=True, sort_keys=True))

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class SourceRankerPort(Protocol):
    def rank(self, ranking_input: RankingInput, *, top_k: int) -> RankingResult: ...
