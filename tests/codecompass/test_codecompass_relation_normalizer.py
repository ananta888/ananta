from __future__ import annotations

from worker.retrieval.codecompass_relation_normalizer import normalize_relation_records


def test_relation_normalizer_handles_resolved_unresolved_and_malformed_rows():
    payload = normalize_relation_records(
        records=[
            {
                "id": "rel-1",
                "relation": "calls_probable_target",
                "source_id": "method:A.retry",
                "target_id": "method:B.execute",
                "confidence": 0.9,
            },
            {
                "id": "rel-2",
                "relation": "injects_dependency",
                "source_id": "type:PaymentService",
                "target_symbol": "PaymentRepository",
            },
            {
                "id": "rel-3",
                "relation": "declares_bean",
                "target_id": "type:AppConfig",
            },
            "bad",
        ],
        manifest_hash="mh-1",
    )

    assert len(payload["resolved_edges"]) == 1
    assert payload["resolved_edges"][0]["edge_type"] == "calls_probable_target"
    assert payload["resolved_edges"][0]["source_id"] == "method:A.retry"
    assert payload["resolved_edges"][0]["target_id"] == "method:B.execute"
    assert len(payload["unresolved_candidates"]) == 1
    assert payload["unresolved_candidates"][0]["resolved_target"] == "PaymentRepository"
    assert payload["malformed_count"] == 2

