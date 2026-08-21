#!/usr/bin/env python3
"""Deterministic Knowledge Hygiene release benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.knowledge_hygiene.analysis import analyze_claims
from ananta_contracts.knowledge_hygiene import CoverageState, KnowledgeClaim, canonical_digest


DEFAULT_FIXTURE = ROOT / "tests/fixtures/knowledge_hygiene/benchmark.v1.json"


def build_claims(fixture: dict[str, object]) -> tuple[KnowledgeClaim, ...]:
    note_count = int(fixture["notes"])
    confirmation_count = int(fixture["confirmations"])
    claims: list[KnowledgeClaim] = []
    for index in range(note_count):
        expected = index % 17
        observed = expected if index < confirmation_count else expected + 1
        for side, value, source_id, digest in (
            ("left", expected, "SRC_0001", "a" * 64),
            ("right", observed, "SRC_0002", "b" * 64),
        ):
            claims.append(
                KnowledgeClaim(
                    claim_id=f"benchmark-{index:04d}-{side}",
                    project_id="benchmark-project",
                    revision=1,
                    subject=f"note-{index:04d}",
                    predicate="replicas",
                    value=value,
                    unit="count",
                    source_id=source_id,
                    source_revision="benchmark-v1",
                    source_locator=f"notes/{index:04d}-{side}.md#replicas",
                    source_content_sha256=digest,
                    extraction_run_id="RUN_0001",
                    coverage=CoverageState.COMPLETE,
                    created_at=1.0,
                )
            )
    return tuple(claims)


def run_benchmark(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, object]:
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    claims = build_claims(fixture)
    started = time.perf_counter()
    result = analyze_claims(
        claims,
        max_candidate_pairs=int(fixture["max_candidate_pairs"]),
        now=1.0,
    )
    duration = time.perf_counter() - started
    expected = int(fixture["expected_conflicts"])
    actual = len(result.conflicts)
    true_positive = min(actual, expected)
    false_positive = max(actual - expected, 0)
    false_negative = max(expected - actual, 0)
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    false_positive_rate = false_positive / max(int(fixture["notes"]) - expected, 1)
    thresholds = dict(fixture["thresholds"])
    passed = (
        len(claims) == int(fixture["claims"])
        and result.coverage is CoverageState.COMPLETE
        and precision >= float(thresholds["precision"])
        and recall >= float(thresholds["recall"])
        and false_positive_rate <= float(thresholds["false_positive_rate_max"])
        and duration <= float(thresholds["latency_seconds_max"])
    )
    return {
        "schema": "knowledge_hygiene_benchmark_result.v1",
        "status": "passed" if passed else "failed",
        "profile": fixture["profile"],
        "fixture_sha256": canonical_digest(fixture),
        "corpus": {
            "notes": fixture["notes"],
            "files": fixture["files"],
            "claims": len(claims),
        },
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_positive_rate": false_positive_rate,
            "coverage": result.coverage.value,
            "conflicts": actual,
            "exact_duplicates": len(result.exact_duplicates),
            "candidate_pairs": result.evaluated_pairs,
            "skipped_pairs": result.skipped_pairs,
            "latency_seconds": round(duration, 6),
            "claims_per_second": round(len(claims) / max(duration, 1e-9), 2),
        },
        "llm_metrics": {
            "measured": False,
            "provider": None,
            "tokens": None,
            "latency_seconds": None,
            "reason_code": "deterministic_offline_profile",
        },
        "thresholds": thresholds,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(args.fixture)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
