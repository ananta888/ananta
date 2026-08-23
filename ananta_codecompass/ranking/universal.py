"""Pure deterministic fusion ranker without repository or product knowledge."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from .contracts import (
    RankedCandidate,
    RankingInput,
    RankingResult,
    ScoreContribution,
)
from .file_roles import classify_file_role

_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "what", "how",
    "der", "die", "das", "und", "mit", "von", "was", "wie", "mir", "den",
    "bitte", "erklaere", "erkläre", "zeige", "show", "explain",
}
_ARCHITECTURE_TERMS = {"architecture", "architektur", "design", "system", "subsystem", "component", "komponente"}
_TEST_TERMS = {"test", "tests", "spec", "coverage", "fixture"}
_ENTRYPOINT_TERMS = {
    "main", "cli", "route", "router", "controller", "endpoint", "handler",
    "register", "registry", "adapter", "port", "tool", "api", "service",
}
_QUERY_SYNONYMS = {
    "architektur": "architecture",
    "komponente": "component",
    "dienst": "service",
    "werkzeug": "tool",
    "fehler": "error",
    "implementierung": "implementation",
}


def _tokens(value: str) -> tuple[str, ...]:
    found: set[str] = set()
    for raw in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ_][A-Za-zÀ-ÖØ-öø-ÿ0-9_-]{1,}", str(value or "")):
        lowered = raw.lower().strip("_-")
        if len(lowered) >= 3 and lowered not in _STOPWORDS:
            found.add(lowered)
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw).replace("_", " ").replace("-", " ")
        for part in expanded.split():
            part_lower = part.lower()
            if len(part_lower) >= 3 and part_lower not in _STOPWORDS:
                found.add(part_lower)
    found.update(_QUERY_SYNONYMS[token] for token in tuple(found) if token in _QUERY_SYNONYMS)
    return tuple(sorted(found))


def _bounded_overlap(query_tokens: set[str], values: list[str] | tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    hits = sorted({token for token in query_tokens if any(token in value.lower() for value in values)})
    denominator = max(1, len(query_tokens))
    return min(1.0, len(hits) / denominator), tuple(hits)


def _entrypoint_score(path: str, symbols: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    stem_tokens = set(_tokens(PurePosixPath(path).stem.replace("-", "_")))
    symbol_tokens = set(_tokens(" ".join(symbols)))
    hits = sorted((stem_tokens | symbol_tokens) & _ENTRYPOINT_TERMS)
    return min(1.0, len(hits) / 2.0), tuple(hits)


def _contribution(signal: str, raw: float, normalized: float, weight: float, evidence: tuple[str, ...] = ()) -> ScoreContribution:
    bounded = min(1.0, max(0.0, float(normalized)))
    return ScoreContribution(signal, float(raw), bounded, float(weight), bounded * float(weight), evidence)


class UniversalSourceRanker:
    """Rank source candidates from declared, bounded signals only."""

    def rank(self, ranking_input: RankingInput, *, top_k: int) -> RankingResult:
        query_tokens = set(_tokens(ranking_input.query))
        if not query_tokens:
            return RankingResult(
                ranking_version=ranking_input.profile.version,
                profile_id=ranking_input.profile.profile_id,
                profile_digest=ranking_input.profile.digest(),
                repository_revision=ranking_input.repository_revision,
                index_digest=ranking_input.index_digest,
                model_digest=ranking_input.model_digest,
                partial_signals=("query_tokens",),
                ranked=(),
            )
        intent_architecture = bool(query_tokens & _ARCHITECTURE_TERMS)
        intent_tests = bool(query_tokens & _TEST_TERMS)
        weights = dict(ranking_input.profile.weights)
        scored: list[RankedCandidate] = []
        for candidate in ranking_input.candidates:
            if not candidate.eligible:
                continue
            role = classify_file_role(candidate.path, candidate.content_excerpt)
            path_parts = [part for part in PurePosixPath(candidate.path).parts]
            filename = PurePosixPath(candidate.path).name
            path_score, path_hits = _bounded_overlap(query_tokens, path_parts)
            filename_score, filename_hits = _bounded_overlap(query_tokens, [filename])
            symbol_score, symbol_hits = _bounded_overlap(query_tokens, list(candidate.symbols))
            exact_hits = tuple(sorted({
                token for token in query_tokens
                if any(token == symbol.lower() for symbol in candidate.symbols)
            }))
            exact_score = 1.0 if exact_hits else 0.0
            entry_score, entry_hits = _entrypoint_score(candidate.path, candidate.symbols)
            if candidate.entrypoint_evidence:
                entry_score = max(entry_score, 1.0)
                entry_hits = tuple(sorted(set(entry_hits) | set(candidate.entrypoint_evidence)))
            centrality = 0.0 if candidate.centrality is None else min(1.0, max(0.0, candidate.centrality))
            graph_proximity = (
                0.0 if candidate.graph_distance is None
                else 1.0 / (1.0 + max(0, candidate.graph_distance))
            )
            role_score = {
                "production": 1.0,
                "unknown": 0.65,
                "documentation": 0.55,
                "test": 0.92 if intent_tests else 0.30,
                "fixture": 0.20,
                "generated": 0.08,
                "vendored": 0.05,
                "build_output": 0.02,
            }[role.role]
            role_penalty = {
                "production": 0.0,
                "unknown": 0.0,
                "documentation": 0.12,
                "test": 0.0 if intent_tests else 0.14,
                "fixture": 0.18,
                "generated": 0.25,
                "vendored": 0.32,
                "build_output": 0.35,
            }[role.role]
            if intent_architecture and role.role == "production":
                role_score = 1.0
            contributions = (
                _contribution("path_lexical", len(path_hits), path_score, weights["path_lexical"], path_hits),
                _contribution("filename_lexical", len(filename_hits), filename_score, weights["filename_lexical"], filename_hits),
                _contribution("symbol_lexical", len(symbol_hits), symbol_score, weights["symbol_lexical"], symbol_hits),
                _contribution("exact_symbol", len(exact_hits), exact_score, weights["exact_symbol"], exact_hits),
                _contribution("structural_role", role_score, role_score, weights["structural_role"], role.reasons),
                _contribution("entrypoint", len(entry_hits), entry_score, weights["entrypoint"], entry_hits),
                _contribution("centrality", centrality, centrality, weights["centrality"], candidate.relation_evidence),
                _contribution("graph_proximity", graph_proximity, graph_proximity, weights["graph_proximity"], candidate.relation_evidence),
                _contribution("role_penalty", role_penalty, role_penalty, weights["role_penalty"], role.reasons),
            )
            lexical_relevance = max(path_score, filename_score, symbol_score, exact_score)
            if lexical_relevance <= 0:
                continue
            score = sum(item.contribution for item in contributions)
            evidence_channels = sum(1 for item in contributions if item.normalized_value > 0)
            confidence = min(1.0, 0.25 + evidence_channels * 0.12) * role.confidence
            scored.append(RankedCandidate(
                candidate=candidate,
                score=round(score, 12),
                confidence=round(confidence, 6),
                file_role=role.role,
                role_confidence=role.confidence,
                role_reasons=role.reasons,
                contributions=contributions,
                tie_breaker=candidate.path.casefold(),
            ))
        scored.sort(key=lambda item: (-item.score, -item.confidence, item.tie_breaker, item.candidate.canonical_id))
        diversified = self._diversify(scored, top_k, ranking_input) if ranking_input.profile.diversification_enabled else scored[:top_k]
        return RankingResult(
            ranking_version=ranking_input.profile.version,
            profile_id=ranking_input.profile.profile_id,
            profile_digest=ranking_input.profile.digest(),
            repository_revision=ranking_input.repository_revision,
            index_digest=ranking_input.index_digest,
            model_digest=ranking_input.model_digest,
            partial_signals=("graph", "embedding", "source_content"),
            ranked=tuple(diversified),
        )

    def _diversify(self, ranked: list[RankedCandidate], top_k: int, ranking_input: RankingInput) -> list[RankedCandidate]:
        selected: list[RankedCandidate] = []
        subtree_counts: Counter[str] = Counter()
        exact_top = bool(ranked and any(c.signal == "exact_symbol" and c.normalized_value == 1.0 for c in ranked[0].contributions))
        for item in ranked:
            subtree = "/".join(PurePosixPath(item.candidate.path).parts[:2])
            repetition = subtree_counts[subtree]
            penalty = (
                0.0 if repetition <= 1 or (not selected and exact_top)
                else min(0.08, (repetition - 1) * 0.04)
            )
            if penalty:
                contribution = ScoreContribution(
                    "diversity_penalty", repetition, penalty,
                    -abs(float(ranking_input.profile.weights["diversity_penalty"])),
                    -penalty, (subtree,),
                )
                item = RankedCandidate(
                    candidate=item.candidate,
                    score=round(item.score - penalty, 12),
                    confidence=item.confidence,
                    file_role=item.file_role,
                    role_confidence=item.role_confidence,
                    role_reasons=item.role_reasons,
                    contributions=(*item.contributions, contribution),
                    tie_breaker=item.tie_breaker,
                )
            selected.append(item)
            subtree_counts[subtree] += 1
        selected.sort(key=lambda item: (-item.score, -item.confidence, item.tie_breaker, item.candidate.canonical_id))
        return selected[:max(1, int(top_k))]
