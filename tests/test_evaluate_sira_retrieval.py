from __future__ import annotations

import pytest

from scripts.evaluate_sira_retrieval import evaluate


def test_evaluation_computes_metrics_and_skips_unverified_labels():
    binding = {"repository_revision": "fixture-v1", "golden_digest": "golden-v1"}
    golden = {
        "binding": binding,
        "queries": [
            {
                "query_id": "q1",
                "query_class": "bugfix",
                "verification_status": "verified",
                "relevance_labels": [{"record_id": "a", "relevance": 2}, {"record_id": "b", "relevance": 1}],
            },
            {"query_id": "q2", "verification_status": "unverified", "relevance_labels": []},
        ],
    }
    baseline = {"binding": binding, "queries": [{"query_id": "q1", "records": [{"record_id": "b"}]}]}
    candidate = {
        "binding": binding,
        "queries": [{"query_id": "q1", "records": [{"record_id": "a"}, {"record_id": "b"}]}],
    }
    report = evaluate(golden=golden, baseline=baseline, candidate=candidate, top_k=2)
    assert report["verified_query_count"] == 1
    assert report["unverified_query_count"] == 1
    assert report["aggregate"]["recall_at_2"]["delta"] == 0.5
    assert report["aggregate"]["mrr"]["candidate"] == 1.0
    assert report["query_classes"]["bugfix"]["verified_query_count"] == 1
    assert report["aggregate"]["mrr"]["delta_ci95"]["method"] == "normal_paired_delta"
    assert report["activation_gate"]["passed"] is False
    assert report["activation_gate"]["reason_codes"] == ["sira_evaluation_policy_missing"]


def test_evaluation_rejects_snapshot_mismatch():
    with pytest.raises(ValueError, match="sira_benchmark_binding_mismatch"):
        evaluate(
            golden={"binding": {"revision": "one"}, "queries": []},
            baseline={"binding": {"revision": "two"}, "queries": []},
            candidate={"binding": {"revision": "one"}, "queries": []},
        )
