"""Read-only comparison contract for universal versus legacy ranking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ShadowRankingComparison:
    repository_revision: str
    index_digest: str
    ranking_version: str
    universal_paths: tuple[str, ...]
    baseline_paths: tuple[str, ...]
    top_k_overlap: float
    latency_ms_universal: float
    latency_ms_baseline: float

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["comparison_digest"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return payload


def compare_rankings(
    *,
    universal_paths: Sequence[str],
    baseline_paths: Sequence[str],
    repository_revision: str,
    index_digest: str,
    ranking_version: str,
    latency_ms_universal: float,
    latency_ms_baseline: float,
) -> ShadowRankingComparison:
    denominator = max(1, min(len(universal_paths), len(baseline_paths)))
    overlap = len(set(universal_paths) & set(baseline_paths)) / denominator
    return ShadowRankingComparison(
        repository_revision=repository_revision,
        index_digest=index_digest,
        ranking_version=ranking_version,
        universal_paths=tuple(universal_paths),
        baseline_paths=tuple(baseline_paths),
        top_k_overlap=overlap,
        latency_ms_universal=round(latency_ms_universal, 3),
        latency_ms_baseline=round(latency_ms_baseline, 3),
    )
