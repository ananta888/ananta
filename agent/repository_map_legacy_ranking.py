"""Legacy (rollback / shadow baseline) ranking of the repository map.

``RepositoryMapEngine._search_legacy`` scores every symbol-graph entry with
the heuristics below, adds path-focus anchors and keeps the focus quota in
the final selection. The heuristics are split per concern (token density,
source-first stem boost, third-party demote, path focus) so each can be
read and tested on its own; the engine only orchestrates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.repository_map_path_focus import path_is_in_focus

# Top-level directories holding documentation, runtime data, build outputs or
# dependencies — never source code that answers architectural questions.
_NON_SOURCE_TOP_DIRS: frozenset[str] = frozenset({
    "docs", "artifacts", "data", "ci-artifacts",
    "autoimport-state", "project-workspaces",
    "todos", "test-reports", "logs",
    "reference_sources", "data_test",
    "ananta.egg-info", "secrets",
    "git-hooks", "node_modules",
    "__pycache__", "venv",
})
# First-party top-level directories outside the core set (tooling, runtime
# assets, reference material); everything else unknown counts as third party.
_FIRST_PARTY_AUXILIARY_TOP_DIRS: frozenset[str] = frozenset({
    "scripts", "public-rendezvous",
    "website", "web", "examples",
    "experiments", "prompts",
})

_MAX_SYMBOL_HITS_PER_TOKEN = 8.0


@dataclass(frozen=True)
class LegacyRankingVocabulary:
    """Path vocabulary of the engine (class attributes, so subclasses can adjust it)."""

    test_path_markers: tuple[str, ...]
    test_file_patterns: tuple[str, ...]
    core_dirs: frozenset
    client_surface_dirs: frozenset


def query_tokens(query: str, stop_tokens: frozenset) -> set[str]:
    return {
        t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query)
        if len(t) >= 3 and t.lower() not in stop_tokens
    }


def _token_density_score(path_lower: str, sym_lower: list[str], tokens: set[str]) -> tuple[float, int]:
    """(score, path token hits). Repeated helper symbols are capped so density never outweighs a path match."""
    score = 0.0
    path_token_hits = 0
    for token in tokens:
        if token in path_lower:
            score += 1.4
            path_token_hits += 1
        score += min(_MAX_SYMBOL_HITS_PER_TOKEN, sum(1.0 for sym in sym_lower if token in sym))
    return score, path_token_hits


def _multi_token_stem_factor(rel_path: str, tokens: set[str], path_token_hits: int) -> float:
    """Boost files whose filename stem matches >= 2 distinct query tokens."""
    if path_token_hits < 2:
        return 1.0
    stem_tokens = set(re.findall(r"[a-z0-9]+", Path(rel_path).stem.lower()))
    stem_hits = len(tokens.intersection(stem_tokens))
    return 1.0 + 0.5 * stem_hits if stem_hits >= 2 else 1.0


def is_test_path(rel_path: str, vocabulary: LegacyRankingVocabulary) -> bool:
    """Test directory marker, ``test_*`` stem or a frontend test file pattern (``*.spec.ts`` …)."""
    path_lower = rel_path.lower()
    return (
        any(marker in path_lower for marker in vocabulary.test_path_markers)
        or Path(rel_path).stem.lower().startswith("test_")
        or any(pat in path_lower for pat in vocabulary.test_file_patterns)
    )


def _source_first_factor(rel_path: str, domain_stems: set[str], vocabulary: LegacyRankingVocabulary) -> float:
    """Source-First Selector: source stems naming a domain token win; unrelated test files are demoted.

    Test files whose stem names the domain keep their natural score: they document behaviour and are useful
    supporting context after the source files.
    """
    stem_text = Path(rel_path).stem.lower()
    test_path = is_test_path(rel_path, vocabulary)
    stem_hit_domains = {d for d in domain_stems if d in stem_text}
    if stem_hit_domains and not test_path:
        return 1.0 + 2.0 * len(stem_hit_domains)
    if test_path and not stem_hit_domains:
        return 0.15
    return 1.0


def is_third_party_path(rel_path: str, vocabulary: LegacyRankingVocabulary) -> bool:
    """Files outside Ananta's core dirs and client surfaces (e.g. client_surfaces/blender/)."""
    top_segment = rel_path.split("/", 1)[0] if "/" in rel_path else rel_path
    if top_segment in vocabulary.core_dirs:
        return False
    if top_segment == "client_surfaces":
        sub = rel_path.split("/", 2)
        return len(sub) >= 2 and sub[1] not in vocabulary.client_surface_dirs
    if top_segment in _NON_SOURCE_TOP_DIRS:
        return True
    return top_segment not in _FIRST_PARTY_AUXILIARY_TOP_DIRS


def _apply_path_focus_boost(score: float, rel_path: str, path_focus: dict | None) -> float:
    if not path_is_in_focus(rel_path, path_focus):
        return score
    score *= 2.4
    if path_is_in_focus(rel_path, path_focus, preferred_only=True):
        score *= 1.35
    return score


def score_legacy_candidate(
    rel_path: str,
    symbols: list[str],
    *,
    tokens: set[str],
    domain_stems: set[str],
    path_focus: dict | None,
    vocabulary: LegacyRankingVocabulary,
) -> float:
    """Legacy relevance of one file; 0 or less means "not a candidate"."""
    path_lower = rel_path.lower()
    score, path_token_hits = _token_density_score(path_lower, [s.lower() for s in symbols], tokens)
    if score <= 0:
        return score
    score *= _multi_token_stem_factor(rel_path, tokens, path_token_hits)
    score *= _source_first_factor(rel_path, domain_stems, vocabulary)
    if is_third_party_path(rel_path, vocabulary):
        score *= 0.2
    return _apply_path_focus_boost(score, rel_path, path_focus)


def _anchor_content(repo_root: Path, anchor_path: str, symbol_summary: str) -> str | None:
    file_content: str | None = None
    try:
        anchor_file = repo_root / anchor_path
        if anchor_file.exists() and anchor_file.is_file():
            file_content = anchor_file.read_text(encoding="utf-8", errors="ignore")[:2000]
    except Exception:
        pass
    if not file_content and not symbol_summary:
        return None
    if not file_content:
        return f"{anchor_path}\nSymbols: {symbol_summary}"
    content_parts = [anchor_path]
    if symbol_summary:
        content_parts.append(f"Symbols: {symbol_summary}")
    content_parts.append(file_content)
    return "\n".join(content_parts)


def add_path_focus_anchors(
    candidates: list[Any],
    *,
    path_focus: dict,
    symbol_by_path: dict[str, list[str]],
    repo_root: Path,
    alias_boost: float,
    make_chunk: Callable[..., Any],
) -> None:
    """Guarantee the path-focus anchor files a score close to the best candidate."""
    candidates_by_source = {chunk.source: chunk for chunk in candidates}
    anchor_paths = [str(path) for path in list(path_focus.get("anchor_paths") or []) if str(path).strip()]
    max_score = max([chunk.score for chunk in candidates], default=1.0)
    anchor_score = max_score * 0.72
    alias_anchor_set = set(path_focus.get("alias_anchor_paths") or [])
    alias_anchor_score = max_score * alias_boost
    focus_id = str(path_focus.get("id") or "")
    for anchor_path in anchor_paths:
        is_alias = anchor_path in alias_anchor_set
        effective_score = alias_anchor_score if is_alias else anchor_score
        existing_anchor = candidates_by_source.get(anchor_path)
        if existing_anchor is not None:
            existing_anchor.score = max(float(existing_anchor.score or 0.0), effective_score)
            existing_anchor.metadata = {**dict(existing_anchor.metadata or {}), "path_focus_anchor": focus_id}
            continue
        symbols = list(symbol_by_path.get(anchor_path) or [])
        chunk_content = _anchor_content(repo_root, anchor_path, ", ".join(symbols[:20]))
        if chunk_content is None:
            continue
        candidates.append(
            make_chunk(
                engine="repository_map",
                source=anchor_path,
                content=chunk_content,
                score=effective_score,
                metadata={
                    "symbol_count": str(len(symbols)),
                    "path_focus_anchor": focus_id,
                    "alias_anchor": "true" if is_alias else "false",
                },
            )
        )
        candidates_by_source[anchor_path] = candidates[-1]


def select_with_path_focus(candidates: list[Any], *, path_focus: dict | None, top_k: int) -> list[Any]:
    """Top-k by score; with a path focus at least ``min_results`` focused files are kept."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    if not path_focus:
        return ranked[:top_k]

    limit = max(1, int(top_k or 1))
    selected = ranked[:limit]
    selected_sources = {chunk.source for chunk in selected}
    focused = [
        chunk for chunk in ranked
        if chunk.source not in selected_sources and path_is_in_focus(chunk.source, path_focus)
    ]
    min_results = max(1, int(path_focus.get("min_results") or 1))
    current_focus_count = sum(1 for chunk in selected if path_is_in_focus(chunk.source, path_focus))
    for chunk in focused:
        if current_focus_count >= min_results:
            break
        if len(selected) >= limit:
            selected.pop()
        selected.append(chunk)
        selected_sources.add(chunk.source)
        current_focus_count += 1
    return sorted(selected, key=lambda c: c.score, reverse=True)[:limit]
