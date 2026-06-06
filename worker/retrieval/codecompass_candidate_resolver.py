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

# Scoring weights per match type
_WEIGHT = {
    "exact_symbol":   2.5,
    "phrase":         1.8,
    "broad_token":    0.6,
    "embedding_text": 1.2,
    "graph_neighbor": 0.8,
    "context_hit":    1.5,
    "details_hit":    1.0,
}

_MAX_CANDIDATES = 40


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

            # Exact symbol matches
            hit_symbols = _tokens_match(exact_symbols, text)
            if hit_symbols:
                weight = _WEIGHT["exact_symbol"] * len(hit_symbols)
                cs.total += weight
                reason = f"exact_symbol:{','.join(hit_symbols[:3])}"
                if reason not in cs.match_reasons:
                    cs.match_reasons.append(reason)
                for s in hit_symbols:
                    if s not in cs.matched_symbols:
                        cs.matched_symbols.append(s)

            # Phrase matches
            hit_phrases = _tokens_match(phrases, text)
            if hit_phrases:
                cs.total += _WEIGHT["phrase"] * len(hit_phrases)
                reason = f"phrase:{','.join(hit_phrases[:2])}"
                if reason not in cs.match_reasons:
                    cs.match_reasons.append(reason)

            # Broad token matches — diminishing returns
            hit_broad = _tokens_match(broad_tokens[:15], text)
            if hit_broad:
                cs.total += _WEIGHT["broad_token"] * math.log1p(len(hit_broad))
                if "broad_token_match" not in cs.match_reasons:
                    cs.match_reasons.append("broad_token_match")

            # Kind-specific bonuses
            if kind == "context":
                cs.total += _WEIGHT["context_hit"]
                if "context_hit" not in cs.match_reasons:
                    cs.match_reasons.append("context_hit")
            elif kind == "details":
                cs.total += _WEIGHT["details_hit"] * 0.5

            # Embedding text match bonus (kind==embedding)
            if kind == "embedding":
                emb_text = str(record.get("embedding_text") or "")
                if emb_text and _tokens_match(exact_symbols or phrases, emb_text):
                    cs.total += _WEIGHT["embedding_text"]
                    if "embedding_text_match" not in cs.match_reasons:
                        cs.match_reasons.append("embedding_text_match")

            # Graph neighbor — adds weight to paths reachable from seed
            if kind == "graph_nodes":
                cs.total += _WEIGHT["graph_neighbor"]
                if "graph_neighbor" not in cs.match_reasons:
                    cs.match_reasons.append("graph_neighbor")

            # relations: add target path as a secondary candidate
            if kind == "relations":
                target_path = _normalize_path(
                    str(record.get("target_path") or record.get("to_path") or "")
                )
                if target_path and target_path != path:
                    tcs = _get(target_path)
                    tcs.total += _WEIGHT["graph_neighbor"] * 0.5
                    tcs.relation_path = path
                    if "relation_neighbor" not in tcs.match_reasons:
                        tcs.match_reasons.append("relation_neighbor")

        if not scores:
            return []

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
