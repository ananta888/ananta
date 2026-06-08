"""
CodeCompassCandidateResolver — CWFH-004

Scores and ranks CandidateFile objects from all CodeCompass output kinds
(embedding, index, details, context, graph_nodes) for a given question.

Does NOT read original file contents — only works with CodeCompass metadata.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_output_reader import (
    CodeCompassOutputReader,
    extract_file_path_from_record,
)
from worker.retrieval.codecompass_query_parser import parse_codecompass_query

# Scoring weights per match type. Note: *_hit scores are applied AT MOST ONCE
# per (path, hit_kind) pair — see _PATH_BONUS_CAPS in resolve() — so that
# large files with hundreds of detail/context records do not flood the ranking
# simply by file size.
_WEIGHT = {
    "exact_symbol":   2.5,
    "phrase":         1.8,
    "broad_token":    0.6,
    "embedding_text": 1.2,
    "graph_neighbor": 0.8,
    "context_hit":    1.5,
    "details_hit":    1.0,
}
# Cap on distinct source paths a single relations record may contribute to.
# The relations graph is dense; a single source path can have 50+ outgoing
# edges, which would otherwise flood the ranking for any query that hits
# the source path's tokens. 3 keeps the graph signal useful without flooding.
_MAX_RELATION_TARGETS_PER_SOURCE = 3
# Cap on incoming relations accepted per target path. A central file
# (e.g. AnantaApiClient.java) can be the `to` of 500+ relations; without
# this cap it would outscore every actual source-of-truth file in the
# codebase. 10 keeps the central-file signal visible but bounded.
_MAX_INCOMING_RELATIONS_PER_TARGET = 10
_MAX_CANDIDATES = 40

# Path-parts that mark a file as test material rather than source code.
# Source-First selector: such files are demoted in the final ranking
# because they reflect how a subsystem is exercised, not how it is built.
# Markers are matched as substrings against the lowered path, so they are
# checked both with and without a leading slash (paths may be relative
# "tests/foo.py" or absolute "/app/tests/foo.py" depending on provenance).
_REPO_TEST_PATH_PARTS = (
    "tests/", "test/", "__tests__/", ".github/workflows/test",
    "spec/", ".spec.", ".test.",
)
_REPO_TEST_PATH_PARTS_LOWER = tuple(p.lower() for p in _REPO_TEST_PATH_PARTS)
# Path-parts that mark a file as build / config / docs material — not source.
_REPO_NONSOURCE_PATH_PARTS = (
    "docs/", "data/", "venv/", "node_modules/", "__pycache__/",
    "project-workspaces/", ".git/", "dist/", "build/",
    "docker-compose", "compose.", ".github/",  # CI workflow files
    "frontend-angular/", "rag-helper/out/", "rag-helper/output/",
    "codecompass-out/", ".claude/", "artifacts/",
    "testsuites/",  # junit xml test cases
    "config/runs/",  # CI run configurations
)
_REPO_NONSOURCE_PATH_PARTS_LOWER = tuple(p.lower() for p in _REPO_NONSOURCE_PATH_PARTS)
# Client-surface prefixes that are third-party integrations (not Ananta-core).
# These are scored but heavily demoted — they happen to share the Ananta
# project root but their internals (e.g. blender/addon/tasks.py) are foreign
# code that just happens to import the same words.
# Ananta-owned client surfaces (operator_tui, tui_runtime, common) and the
# Ananta-branded integrations (eclipse/ananta_eclipse_plugin) are kept at
# full weight — they are first-party code, not third-party ports.
_REPO_THIRDPARTY_CLIENT_PREFIXES = (
    "client_surfaces/blender/",
    "client_surfaces/freecad/",
    "client_surfaces/nvim_runtime/",
    "client_surfaces/vim_compat/",
    "client_surfaces/vscode_extension/",
)
# Minimum length of a token in a path-stem that may trigger the source stem
# boost. Avoids boosting on 1-2 char filenames like x.py.
_MIN_STEM_TOKEN_LEN = 4


def _is_test_path(path_lower: str) -> bool:
    return any(m in path_lower for m in _REPO_TEST_PATH_PARTS_LOWER)


def _is_nonsource_path(path_lower: str) -> bool:
    if any(m in path_lower for m in _REPO_NONSOURCE_PATH_PARTS_LOWER):
        return True
    for prefix in _REPO_THIRDPARTY_CLIENT_PREFIXES:
        if path_lower.startswith(prefix.lower()):
            return True
    return False


def _is_source_path(path: str) -> bool:
    path_lower = path.lower()
    if _is_test_path(path_lower):
        return False
    if _is_nonsource_path(path_lower):
        return False
    return True


def _stem_tokens(path: str) -> set[str]:
    """Return the lowercase tokens of the file stem (filename without ext).

    Tokens are split on every non-alphanumeric character (so
    "codecompass_candidate_resolver" yields {"codecompass", "candidate",
    "resolver"}), with single-character tokens dropped. This lets the
    source-stem boost match the user's natural-language query token
    "codecompass" against the file's filename even when the filename uses
    snake_case.
    """
    from pathlib import Path as _P
    stem = _P(path).stem.lower()
    return {t for t in re.findall(r"[a-z0-9]+", stem) if len(t) >= _MIN_STEM_TOKEN_LEN}


def _source_path_multiplier(path: str) -> float:
    """Source-First selector multiplier for a path.

    Source files with a domain token in the stem get a boost; test files
    with no domain token in the stem are demoted; non-source paths (docs,
    data, docker-compose, github workflows) are heavily demoted.
    """
    path_lower = path.lower()
    if _is_nonsource_path(path_lower):
        return 0.2
    if _is_test_path(path_lower):
        return 0.3
    return 1.0  # source path — boost applied separately via stem_match


def _stem_boost(path: str, query_tokens: set[str]) -> float:
    """Source-stem boost: 1.5× when the file stem contains ANY query token
    (from exact_symbols, phrases, OR broad_terms), else 1.0×.

    Test and non-source paths get 1.0× here — their multiplier is applied
    separately. Broad-token overlap with the stem is a very strong signal
    (the user literally named the subsystem in the file's filename) so we
    treat it the same as exact-symbol overlap.
    """
    if not _is_source_path(path):
        return 1.0
    stem = _stem_tokens(path)
    if not stem or not query_tokens:
        return 1.0
    lowered_query = {t.lower() for t in query_tokens}
    if stem & lowered_query:
        return 1.5
    return 1.0


_PATH_LIKE_RE = re.compile(r"[/\\.]")


def _looks_like_path(s: str) -> bool:
    """Heuristic: does `s` look like a repository-relative file path?

    A path-like string contains a path separator ("/" or "\\") or a file
    extension (".py", ".java", ".md", etc.). This filters out bare symbols
    that show up as relation targets ("String", "void", "File", "IOException",
    "md_file:a03585ab89eed5e0") which the resolver must not treat as paths.
    """
    if not s:
        return False
    return bool(_PATH_LIKE_RE.search(s))


@dataclass
class CandidateScore:
    path: str
    total: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    source_record_ids: list[str] = field(default_factory=list)
    source_output_kinds: list[str] = field(default_factory=list)
    matched_symbols: list[str] = field(default_factory=list)
    relation_path: str | None = None
    manifest_hash: str | None = None
    # Track which bonus kinds have already been applied for this path so the
    # *_hit and graph_neighbor bonuses fire at most once per (path, kind).
    applied_bonuses: set[str] = field(default_factory=set)


def _normalize_path(raw: str) -> str:
    return str(raw or "").strip().lstrip("/")


def _tokens_match(terms: list[str], text: str) -> list[str]:
    """Return which terms appear (case-insensitive) in text."""
    low = text.lower()
    return [t for t in terms if t.lower() in low]


class CodeCompassCandidateResolver:
    """
    Resolves a ranked list of CandidateFile dicts from CodeCompass outputs.

    Usage:
        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="Wo wird TaskRoutingContract definiert?",
            output_dir="/path/to/codecompass/outputs",
        )
    """

    def __init__(self, max_candidates: int = _MAX_CANDIDATES):
        self._max = max(1, max_candidates)
        self._reader = CodeCompassOutputReader()

    def resolve(
        self,
        *,
        question: str,
        output_dir: str | Path,
        memory_context: str | None = None,
        manifest_hash: str | None = None,
        graph_expansion_profile: str = "bugfix_local",
    ) -> list[dict[str, Any]]:
        """
        Return a ranked list of CandidateFile dicts (CWFH-003 schema).
        """
        output_dir = Path(output_dir)
        if not output_dir.exists():
            return []

        loaded = self._reader.load_from_output_dir(output_dir=output_dir)
        records: list[dict[str, Any]] = loaded.get("records") or []
        mhash = manifest_hash or str((loaded.get("manifest") or {}).get("manifest_hash") or "")

        parsed = parse_codecompass_query(question)
        exact_symbols = parsed["exact_symbol_terms"]
        phrases = parsed["phrase_terms"]
        broad_tokens = parsed["broad_terms"]

        if memory_context:
            mem_parsed = parse_codecompass_query(memory_context)
            broad_tokens = list({*broad_tokens, *mem_parsed["broad_terms"]})[:30]

        scores: dict[str, CandidateScore] = {}

        def _get(path: str) -> CandidateScore:
            if path not in scores:
                scores[path] = CandidateScore(path=path, manifest_hash=mhash)
            return scores[path]

        for record in records:
            prov = record.get("_provenance") or {}
            kind = str(prov.get("output_kind") or "index")
            record_id = str(prov.get("record_id") or record.get("id") or "")

            path = extract_file_path_from_record(record, output_kind=kind)
            if not path:
                continue
            path = _normalize_path(path)

            cs = _get(path)
            if record_id and record_id not in cs.source_record_ids:
                cs.source_record_ids.append(record_id)
            if kind not in cs.source_output_kinds:
                cs.source_output_kinds.append(kind)

            # Build searchable text from record
            text_parts: list[str] = []
            for field_name in ("symbol", "name", "summary", "content", "embedding_text",
                               "title", "description", "path", "file"):
                val = record.get(field_name)
                if val and isinstance(val, str):
                    text_parts.append(val)
            text = " ".join(text_parts)

            # Exact symbol matches — applied at most once per (path,
            # exact_symbol_set). The per-symbol weight inside the set still
            # counts every unique symbol that was hit, but a file with 100
            # records of the same symbol doesn't get 100× the weight of a
            # file with 1 record of the same symbol.
            if "exact_symbol" not in cs.applied_bonuses:
                hit_symbols = _tokens_match(exact_symbols, text)
                if hit_symbols:
                    weight = _WEIGHT["exact_symbol"] * len(hit_symbols)
                    cs.total += weight
                    cs.applied_bonuses.add("exact_symbol")
                    reason = f"exact_symbol:{','.join(hit_symbols[:3])}"
                    if reason not in cs.match_reasons:
                        cs.match_reasons.append(reason)
                    for s in hit_symbols:
                        if s not in cs.matched_symbols:
                            cs.matched_symbols.append(s)

            # Phrase matches — same once-per-path semantics.
            if "phrase" not in cs.applied_bonuses:
                hit_phrases = _tokens_match(phrases, text)
                if hit_phrases:
                    cs.total += _WEIGHT["phrase"] * len(hit_phrases)
                    cs.applied_bonuses.add("phrase")
                    reason = f"phrase:{','.join(hit_phrases[:2])}"
                    if reason not in cs.match_reasons:
                        cs.match_reasons.append(reason)

            # Broad token matches — diminishing returns.
            # Per-path cap: apply AT MOST ONCE per (path, "broad_token_match").
            # Otherwise large files with hundreds of records flood the
            # ranking for any query that shares even one common short token
            # (e.g. "re" or "de") with the file's symbols, embeddings, or
            # content snippets.
            if "broad_token_match" not in cs.applied_bonuses:
                hit_broad = _tokens_match(broad_tokens[:15], text)
                if hit_broad:
                    cs.total += _WEIGHT["broad_token"] * math.log1p(len(hit_broad))
                    cs.applied_bonuses.add("broad_token_match")
                    if "broad_token_match" not in cs.match_reasons:
                        cs.match_reasons.append("broad_token_match")

            # Per-path kind-bonus caps: context_hit, details_hit, and
            # graph_neighbor (from graph_nodes) are applied AT MOST ONCE
            # per (path, bonus_kind). This stops a single large file with
            # 200+ details records from outscoring a small file with 1
            # details record just because of file size.
            if kind == "context" and "context_hit" not in cs.applied_bonuses:
                cs.total += _WEIGHT["context_hit"]
                cs.applied_bonuses.add("context_hit")
                if "context_hit" not in cs.match_reasons:
                    cs.match_reasons.append("context_hit")
            elif kind == "details" and "details_hit" not in cs.applied_bonuses:
                cs.total += _WEIGHT["details_hit"]
                cs.applied_bonuses.add("details_hit")
                if "details_hit" not in cs.match_reasons:
                    cs.match_reasons.append("details_hit")

            # Embedding text match bonus (kind==embedding) — per-symbol, capped
            # at 1× per (path, "embedding_text_match") so the textual bonus
            # reflects the existence of an embedding match, not its count.
            if kind == "embedding":
                emb_text = str(record.get("embedding_text") or "")
                if emb_text and _tokens_match(exact_symbols or phrases, emb_text):
                    if "embedding_text_match" not in cs.applied_bonuses:
                        cs.total += _WEIGHT["embedding_text"]
                        cs.applied_bonuses.add("embedding_text_match")
                        if "embedding_text_match" not in cs.match_reasons:
                            cs.match_reasons.append("embedding_text_match")

            # Graph neighbor (from graph_nodes) — once per path.
            if kind == "graph_nodes" and "graph_neighbor_node" not in cs.applied_bonuses:
                cs.total += _WEIGHT["graph_neighbor"]
                cs.applied_bonuses.add("graph_neighbor_node")
                if "graph_neighbor" not in cs.match_reasons:
                    cs.match_reasons.append("graph_neighbor")

            # Relations: cap distinct target paths per source path AND
            # distinct source paths per target. Without the source cap, a
            # hub file with 50 outgoing relations dumps 0.4 points on 50
            # unrelated targets for every query. Without the target cap, a
            # central file (e.g. AnantaApiClient.java) accumulates 0.4
            # points from every other Ananta module that calls into it,
            # again for every query. Both caps together keep the graph
            # signal useful without flooding.
            # Also guard against Java/C# type symbols ("String", "void", "File",
            # "IOException") that show up as `to` values in relations records
            # and would otherwise pollute the path set with non-paths.
            if kind == "relations":
                relation_source = _normalize_path(
                    str(record.get("from") or record.get("source") or record.get("from_path") or path)
                )
                rel_out_key = f"relation_targets:{relation_source}"
                if cs.match_reasons.count(rel_out_key) < _MAX_RELATION_TARGETS_PER_SOURCE:
                    target_path = _normalize_path(
                        str(record.get("to") or record.get("target") or record.get("target_path") or record.get("to_path") or "")
                    )
                    if target_path and target_path != path and _looks_like_path(target_path):
                        tcs = _get(target_path)
                        # Cap incoming relations per target path.
                        rel_in_key = "relation_incoming_total"
                        if tcs.match_reasons.count(rel_in_key) < _MAX_INCOMING_RELATIONS_PER_TARGET:
                            tcs.total += _WEIGHT["graph_neighbor"] * 0.5
                            tcs.relation_path = path
                            if "relation_neighbor" not in tcs.match_reasons:
                                tcs.match_reasons.append("relation_neighbor")
                            tcs.match_reasons.append(rel_in_key)  # counter
                            if rel_out_key not in tcs.match_reasons:
                                tcs.match_reasons.append(rel_out_key)

        if not scores:
            return []

        # Source-First selector: apply path-class multiplier and stem boost
        # to the accumulated raw scores. We do this once at the end (not
        # inside the per-record loop) so the per-record bookkeeping stays
        # clean and the multiplier can be inspected in tests.
        all_query_tokens = set(exact_symbols) | set(phrases) | set(broad_tokens)
        for cs in scores.values():
            path_mult = _source_path_multiplier(cs.path)
            stem_boost = _stem_boost(cs.path, all_query_tokens)
            cs.total *= path_mult * stem_boost

        sorted_candidates = sorted(scores.values(), key=lambda c: c.total, reverse=True)
        top = sorted_candidates[: self._max]

        return [
            {
                "path": c.path,
                "score": round(c.total, 4),
                "reason": "; ".join(c.match_reasons[:5]) or "indirect_match",
                "source_record_ids": c.source_record_ids[:10],
                "source_output_kinds": sorted(set(c.source_output_kinds)),
                "matched_symbols": c.matched_symbols[:10],
                "relation_path": c.relation_path,
                "manifest_hash": c.manifest_hash,
                "sensitivity": "internal",
                "read_policy": "allowed",
                "requires_read": bool(c.matched_symbols or "context_hit" in c.match_reasons),
            }
            for c in top
            if c.total > 0
        ]
