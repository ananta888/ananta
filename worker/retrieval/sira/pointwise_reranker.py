from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class PointwiseScorePort(Protocol):
    @property
    def model_digest(self) -> str: ...

    def score(self, *, query: str, candidate: Mapping[str, Any]) -> float: ...


class PointwiseReranker:
    def __init__(self, *, scorer: PointwiseScorePort | None, timeout_ms: int, weight: float = 0.2):
        self._scorer = scorer
        self._timeout_ms = max(100, int(timeout_ms))
        self._weight = max(0.0, min(1.0, float(weight)))

    def rerank(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        top_n: int,
    ) -> tuple[Sequence[Mapping[str, Any]], Mapping[str, Any]]:
        original = [dict(item) for item in candidates]
        if self._scorer is None:
            return original, {"status": "skipped", "reason": "reranker_unavailable", "model_digest": ""}
        started = time.monotonic()
        reranked: list[dict[str, Any]] = []
        try:
            for index, item in enumerate(original):
                if index >= max(1, int(top_n)):
                    reranked.append(item)
                    continue
                if (time.monotonic() - started) * 1_000 > self._timeout_ms:
                    return original, {
                        "status": "fallback",
                        "reason": "reranker_timeout",
                        "model_digest": str(self._scorer.model_digest),
                    }
                minimal = {
                    "record_id": str(item.get("record_id") or ""),
                    "path": str(item.get("path") or "")[:512],
                    "content": str(item.get("content") or "")[:2_000],
                    "lexical_score": float(item.get("score") or 0.0),
                }
                pointwise = max(0.0, min(1.0, float(self._scorer.score(query=query, candidate=minimal))))
                metadata = dict(item.get("metadata") or {})
                metadata["sira_pointwise_score"] = pointwise
                metadata["sira_pointwise_model_digest"] = str(self._scorer.model_digest)
                reranked.append(
                    {
                        **item,
                        "score": float(item.get("score") or 0.0) + pointwise * self._weight,
                        "metadata": metadata,
                    }
                )
        except Exception:
            return original, {
                "status": "fallback",
                "reason": "reranker_error",
                "model_digest": str(self._scorer.model_digest),
            }
        reranked.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("record_id") or "")))
        return reranked, {
            "status": "ready",
            "reason": "reranked",
            "model_digest": str(self._scorer.model_digest),
            "candidate_count": min(len(original), max(1, int(top_n))),
        }
