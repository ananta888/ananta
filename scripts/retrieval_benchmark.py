from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any


RETRIEVAL_BENCHMARK_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "bugfix-timeout",
        "task_kind": "bugfix",
        "bundle_mode": "standard",
        "query": "timeout retry failure in worker pipeline",
        "expected_markers": ["timeout", "retry", "worker", "error"],
    },
    {
        "id": "refactor-symbol-neighborhood",
        "task_kind": "refactor",
        "bundle_mode": "standard",
        "query": "refactor service boundary and symbol dependencies",
        "expected_markers": ["refactor", "symbol", "dependency", "service"],
    },
    {
        "id": "architecture-overview",
        "task_kind": "architecture",
        "bundle_mode": "full",
        "query": "architecture overview and decision records for orchestration",
        "expected_markers": ["architecture", "adr", "overview", "orchestration"],
    },
    {
        "id": "config-xml-integration",
        "task_kind": "config",
        "bundle_mode": "standard",
        "query": "xml config integration mapping and runtime constraints",
        "expected_markers": ["xml", "config", "mapping", "runtime"],
    },
]


@dataclass
class ScenarioScore:
    scenario_id: str
    task_kind: str
    bundle_mode: str
    chunk_count: int
    token_estimate: int
    duplicate_rate: float
    noise_rate: float
    retrieval_utilization: float
    marker_coverage: float
    score: float


def _marker_coverage(context_text: str, markers: list[str]) -> float:
    if not markers:
        return 0.0
    text = str(context_text or "").lower()
    hits = 0
    for marker in markers:
        token = str(marker or "").strip().lower()
        if token and token in text:
            hits += 1
    return round(float(hits) / float(len(markers)), 4)


def evaluate_payload_against_scenario(payload: dict[str, Any], scenario: dict[str, Any]) -> ScenarioScore:
    strategy = dict(payload.get("strategy") or {})
    fusion = dict(strategy.get("fusion") or {})
    dedupe = dict(fusion.get("dedupe") or {})
    candidate_counts = dict(fusion.get("candidate_counts") or {})
    all_candidates = max(1, int(candidate_counts.get("all") or 0))
    final_candidates = max(0, int(candidate_counts.get("final") or 0))
    duplicate_rate = min(1.0, float((dedupe.get("identity_duplicates") or 0) + (dedupe.get("content_duplicates") or 0)) / float(all_candidates))
    noise_rate = min(1.0, max(0.0, float(all_candidates - final_candidates) / float(all_candidates)))
    budget = dict(payload.get("budget") or {})
    retrieval_utilization = float(budget.get("retrieval_utilization") or 0.0)
    coverage = _marker_coverage(str(payload.get("context_text") or ""), list(scenario.get("expected_markers") or []))

    score = (
        (coverage * 0.45)
        + ((1.0 - duplicate_rate) * 0.2)
        + ((1.0 - noise_rate) * 0.2)
        + (min(1.0, retrieval_utilization) * 0.15)
    )
    return ScenarioScore(
        scenario_id=str(scenario.get("id") or "unknown"),
        task_kind=str(scenario.get("task_kind") or "unknown"),
        bundle_mode=str(scenario.get("bundle_mode") or "standard"),
        chunk_count=len(payload.get("chunks") or []),
        token_estimate=int(payload.get("token_estimate") or 0),
        duplicate_rate=round(duplicate_rate, 4),
        noise_rate=round(noise_rate, 4),
        retrieval_utilization=round(retrieval_utilization, 4),
        marker_coverage=coverage,
        score=round(score, 4),
    )


def aggregate_scores(scores: list[ScenarioScore]) -> dict[str, Any]:
    if not scores:
        return {"count": 0, "average_score": 0.0, "items": []}
    average = round(sum(item.score for item in scores) / float(len(scores)), 4)
    by_task_kind: dict[str, list[float]] = {}
    for score in scores:
        by_task_kind.setdefault(score.task_kind, []).append(score.score)
    by_task_kind_avg = {
        key: round(sum(values) / float(len(values)), 4)
        for key, values in sorted(by_task_kind.items(), key=lambda item: item[0])
    }
    return {
        "count": len(scores),
        "average_score": average,
        "by_task_kind": by_task_kind_avg,
        "items": [score.__dict__ for score in scores],
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval payloads against benchmark scenarios.")
    parser.add_argument("--payload-file", required=True, help="Path to JSON file with payloads by scenario id.")
    args = parser.parse_args()

    with open(args.payload_file, "r", encoding="utf-8") as handle:
        payload_map = json.load(handle)
    if not isinstance(payload_map, dict):
        raise ValueError("payload-file must contain a JSON object keyed by scenario id")

    scores: list[ScenarioScore] = []
    for scenario in RETRIEVAL_BENCHMARK_SCENARIOS:
        scenario_id = str(scenario.get("id") or "")
        payload = payload_map.get(scenario_id)
        if not isinstance(payload, dict):
            continue
        scores.append(evaluate_payload_against_scenario(payload, scenario))
    print(json.dumps(aggregate_scores(scores), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
