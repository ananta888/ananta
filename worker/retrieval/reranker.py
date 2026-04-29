from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reranker:
    enabled: bool = False
    weight: float = 0.15

    def rerank(self, *, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.enabled:
            return [dict(item) for item in list(candidates or [])]
        query_tokens = {token.lower() for token in str(query or "").split() if token.strip()}
        scored: list[dict[str, Any]] = []
        for item in list(candidates or []):
            text = str(item.get("text") or "")
            overlap = 0.0
            if query_tokens:
                text_tokens = {token.lower() for token in text.split() if token.strip()}
                overlap = float(len(query_tokens.intersection(text_tokens))) / float(len(query_tokens))
            boosted = dict(item)
            boosted["final_score"] = float(item.get("final_score") or 0.0) + overlap * float(self.weight)
            boosted["rerank_overlap"] = overlap
            scored.append(boosted)
        return sorted(scored, key=lambda candidate: float(candidate.get("final_score") or 0.0), reverse=True)

