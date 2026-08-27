from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class SiraHybridAdapter:
    """Adapt SIRA rows to the existing codecompass_fts channel contract."""

    @staticmethod
    def adapt(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        adapted: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            metadata = dict(item.get("metadata") or {})
            metadata["retrieval_profile"] = "corpus_discriminative_lexical"
            metadata["lexical_contributions"] = {
                "original_bm25": float(metadata.get("bm25_score") or 0.0),
                "sira_expansion": float(metadata.get("sira_weighted_score") or 0.0),
                "pointwise_rerank": float(metadata.get("sira_pointwise_score") or 0.0),
            }
            adapted.append(
                {
                    **item,
                    "engine": "codecompass_fts",
                    "channel": "codecompass_fts",
                    "metadata": metadata,
                }
            )
        return adapted
