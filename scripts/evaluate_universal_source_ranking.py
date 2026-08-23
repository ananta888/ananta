#!/usr/bin/env python3
"""Evaluate the deterministic universal ranker against its golden scenarios."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ananta_codecompass.ranking import RankingCandidate, RankingInput, UniversalSourceRanker  # noqa: E402
from ananta_codecompass.ranking.evaluation import ranking_metrics  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "scenarios" / "universal_source_ranking.v1.json"


def evaluate() -> dict:
    fixture = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows = []
    for repository in fixture["repositories"]:
        candidates = tuple(
            RankingCandidate(
                canonical_id=item["path"],
                path=item["path"],
                symbols=tuple(item["symbols"]),
                language=repository["language"],
            )
            for item in repository["candidates"]
        )
        for query in repository["queries"]:
            result = UniversalSourceRanker().rank(
                RankingInput(query=query["query"], candidates=candidates),
                top_k=query["k"],
            )
            paths = [item.candidate.path for item in result.ranked]
            rows.append({
                "repository": repository["id"],
                "language": repository["language"],
                "query": query["query"],
                "ranked": paths,
                "metrics": ranking_metrics(paths, set(query["relevant"]), k=query["k"]),
                "canonical_result": result.canonical_json(),
            })
    return {"schema": "codecompass.universal-ranking-evaluation.v1", "results": rows}


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2, sort_keys=True))
