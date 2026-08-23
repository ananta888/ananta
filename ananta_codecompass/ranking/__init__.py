"""Deterministic, repository-independent source ranking."""

from .contracts import (
    RankingCandidate,
    RankingInput,
    RankingProfile,
    RankingResult,
    ScoreContribution,
)
from .universal import UniversalSourceRanker

__all__ = [
    "RankingCandidate",
    "RankingInput",
    "RankingProfile",
    "RankingResult",
    "ScoreContribution",
    "UniversalSourceRanker",
]
