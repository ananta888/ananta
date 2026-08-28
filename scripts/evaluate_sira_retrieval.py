#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence


def _dcg(relevances: Sequence[float]) -> float:
    return sum(float(relevance) / math.log2(index + 2.0) for index, relevance in enumerate(relevances))


def _metrics(*, records: Sequence[Mapping[str, Any]], relevant: Mapping[str, float], top_k: int) -> dict[str, Any]:
    selected = [str(record.get("record_id") or "") for record in records[:top_k]]
    relevant_ids = {record_id for record_id, relevance in relevant.items() if float(relevance) > 0.0}
    hits = [record_id for record_id in selected if record_id in relevant_ids]
    reciprocal_rank = next(
        (1.0 / float(index + 1) for index, record_id in enumerate(selected) if record_id in relevant_ids),
        0.0,
    )
    observed = [float(relevant.get(record_id, 0.0)) for record_id in selected]
    ideal = sorted((float(value) for value in relevant.values()), reverse=True)[:top_k]
    ideal_dcg = _dcg(ideal)
    return {
        f"recall_at_{top_k}": len(set(hits)) / float(max(1, len(relevant_ids))),
        f"ndcg_at_{top_k}": _dcg(observed) / ideal_dcg if ideal_dcg else 0.0,
        "mrr": reciprocal_rank,
        "evidence_coverage": len(set(hits)) / float(max(1, len(relevant_ids))),
    }


def evaluate(
    *,
    golden: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    top_k: int = 10,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    golden_binding = dict(golden.get("binding") or {})
    if dict(baseline.get("binding") or {}) != golden_binding or dict(candidate.get("binding") or {}) != golden_binding:
        raise ValueError("sira_benchmark_binding_mismatch")
    baseline_queries = {str(item.get("query_id") or ""): item for item in list(baseline.get("queries") or [])}
    candidate_queries = {str(item.get("query_id") or ""): item for item in list(candidate.get("queries") or [])}
    rows: list[dict[str, Any]] = []
    for scenario in list(golden.get("queries") or []):
        query_id = str(scenario.get("query_id") or "")
        verification = str(scenario.get("verification_status") or "unverified")
        if verification != "verified":
            rows.append({"query_id": query_id, "verification_status": verification, "metrics": None})
            continue
        relevant = {
            str(item.get("record_id") or ""): float(item.get("relevance") or 0.0)
            for item in list(scenario.get("relevance_labels") or [])
        }
        base = _metrics(
            records=list((baseline_queries.get(query_id) or {}).get("records") or []),
            relevant=relevant,
            top_k=top_k,
        )
        sira = _metrics(
            records=list((candidate_queries.get(query_id) or {}).get("records") or []),
            relevant=relevant,
            top_k=top_k,
        )
        rows.append(
            {
                "query_id": query_id,
                "query_class": str(scenario.get("query_class") or "unknown"),
                "repository_id": str(
                    scenario.get("repository_id")
                    or golden_binding.get("repository_id")
                    or golden_binding.get("repository_revision")
                    or "unknown"
                ),
                "language": str(scenario.get("language") or "unknown"),
                "verification_status": verification,
                "baseline": base,
                "candidate": sira,
                "delta": {key: sira[key] - base[key] for key in base},
            }
        )
    verified = [row for row in rows if row.get("metrics", True) is not None]
    keys = [f"recall_at_{top_k}", f"ndcg_at_{top_k}", "mrr", "evidence_coverage"]
    aggregate = {
        key: {
            "baseline": sum(float(row["baseline"][key]) for row in verified) / float(max(1, len(verified))),
            "candidate": sum(float(row["candidate"][key]) for row in verified) / float(max(1, len(verified))),
        }
        for key in keys
    }
    for values in aggregate.values():
        values["delta"] = values["candidate"] - values["baseline"]
    for key, values in aggregate.items():
        values["delta_ci95"] = _mean_ci95([float(row["delta"][key]) for row in verified])
    query_classes = _groups(verified, key="query_class", metric_keys=keys)
    repositories = _groups(verified, key="repository_id", metric_keys=keys)
    report = {
        "schema": "codecompass.sira-evaluation.v1",
        "binding": golden_binding,
        "top_k": top_k,
        "verified_query_count": len(verified),
        "unverified_query_count": len(rows) - len(verified),
        "aggregate": aggregate,
        "query_classes": query_classes,
        "repositories": repositories,
        "queries": rows,
        "efficiency": {
            "baseline": dict(baseline.get("efficiency") or {}),
            "candidate": dict(candidate.get("efficiency") or {}),
        },
    }
    if policy is None:
        report["activation_gate"] = {
            "passed": False,
            "policy_sha256": "",
            "reason_codes": ["sira_evaluation_policy_missing"],
        }
    else:
        from agent.services.codecompass_sira_evaluation_gate import CodeCompassSiraEvaluationGate

        report["activation_gate"] = CodeCompassSiraEvaluationGate().assess(report, policy).to_dict()
    return report


def _groups(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
    metric_keys: Sequence[str],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
    result: dict[str, Any] = {}
    for group_id, members in sorted(grouped.items()):
        values: dict[str, Any] = {"verified_query_count": len(members)}
        for metric in metric_keys:
            baseline = statistics.fmean(float(item["baseline"][metric]) for item in members)
            candidate = statistics.fmean(float(item["candidate"][metric]) for item in members)
            values[metric] = {
                "baseline": baseline,
                "candidate": candidate,
                "delta": candidate - baseline,
                "delta_ci95": _mean_ci95([float(item["delta"][metric]) for item in members]),
            }
        result[group_id] = values
    return result


def _mean_ci95(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"lower": -1.0, "upper": 1.0, "method": "normal_paired_delta"}
    mean = statistics.fmean(values)
    margin = 0.0 if len(values) == 1 else 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "lower": max(-1.0, mean - margin),
        "upper": min(1.0, mean + margin),
        "method": "normal_paired_delta",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate bound baseline and SIRA retrieval result sets.")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--policy",
        default="config/retrieval/codecompass-sira-evaluation-policy.v1.json",
    )
    args = parser.parse_args()
    payload = evaluate(
        golden=json.loads(Path(args.golden).read_text(encoding="utf-8")),
        baseline=json.loads(Path(args.baseline).read_text(encoding="utf-8")),
        candidate=json.loads(Path(args.candidate).read_text(encoding="utf-8")),
        top_k=max(1, int(args.top_k)),
        policy=json.loads(Path(args.policy).read_text(encoding="utf-8")),
    )
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
