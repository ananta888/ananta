"""Universal final-source ordering for heterogeneous retrieval chunks."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ananta_codecompass.ranking import RankingCandidate, RankingInput, UniversalSourceRanker
from ananta_codecompass.ranking.file_roles import language_for_path


class CodeCompassHybridSourceRankingService:
    """Order already-admitted chunks without widening retrieval scope."""

    def __init__(self, *, ranker: Any | None = None) -> None:
        self._ranker = ranker or UniversalSourceRanker()

    def rank(self, *, query: str, chunks: list[Any]) -> tuple[list[Any], dict[str, Any]]:
        if not chunks:
            return [], {}
        candidates: list[RankingCandidate] = []
        chunk_by_id: dict[str, Any] = {}
        for index, chunk in enumerate(chunks):
            is_mapping = isinstance(chunk, dict)
            path = str(
                (chunk.get("source") if is_mapping else getattr(chunk, "source", "")) or ""
            ).removeprefix("/app/")
            canonical_id = f"{path}#{index}"
            content = str(
                (chunk.get("content") if is_mapping else getattr(chunk, "content", "")) or ""
            )
            symbol_line = next(
                (line for line in content.splitlines()[:4] if line.lower().startswith("symbols:")),
                "",
            )
            symbols = tuple(
                sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", symbol_line)))
            )[:80]
            candidates.append(RankingCandidate(
                canonical_id=canonical_id,
                path=path,
                symbols=symbols,
                language=language_for_path(path),
            ))
            chunk_by_id[canonical_id] = chunk
        digest = hashlib.sha256(
            json.dumps([(item.path, item.symbols) for item in candidates], sort_keys=True).encode("utf-8")
        ).hexdigest()
        result = self._ranker.rank(
            RankingInput(query=query, candidates=tuple(candidates), index_digest=digest),
            top_k=len(candidates),
        )
        ranked_ids = [item.candidate.canonical_id for item in result.ranked]
        ordered = [chunk_by_id[item_id] for item_id in ranked_ids]
        ordered.extend(chunk for item_id, chunk in chunk_by_id.items() if item_id not in set(ranked_ids))
        trace = result.as_dict()
        trace["strategy"] = "universal_hybrid_final"
        trace["admitted_candidate_count"] = len(candidates)
        trace["ranked_candidate_count"] = len(ranked_ids)
        return ordered, trace


_SERVICE = CodeCompassHybridSourceRankingService()


def get_codecompass_hybrid_source_ranking_service() -> CodeCompassHybridSourceRankingService:
    return _SERVICE
