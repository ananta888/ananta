from __future__ import annotations

from worker.retrieval.retrieval_service import HybridRetrievalService


def test_worker_hybrid_merge_deduplicates_same_record_and_keeps_channel_contributions():
    service = HybridRetrievalService()
    payload = service.retrieve(
        query="retry timeout payment",
        pipeline_contract={
            "channels": ["codecompass_fts", "codecompass_vector", "codecompass_graph", "lexical"],
            "fallback_order": ["codecompass_fts", "codecompass_vector", "codecompass_graph", "lexical"],
        },
        channel_results={
            "codecompass_fts": [
                {
                    "path": "src/PaymentService.java",
                    "record_id": "method:PaymentService.retryTimeout",
                    "content_hash": "method:PaymentService.retryTimeout",
                    "score": 0.9,
                    "metadata": {"record_id": "method:PaymentService.retryTimeout", "record_kind": "java_method"},
                }
            ],
            "codecompass_vector": [
                {
                    "path": "src/PaymentService.java",
                    "record_id": "method:PaymentService.retryTimeout",
                    "content_hash": "vector:dup",
                    "score": 0.7,
                    "metadata": {"record_id": "method:PaymentService.retryTimeout", "record_kind": "java_method"},
                }
            ],
            "lexical": [{"path": "docs/payment.md", "content_hash": "lex-1", "score": 0.2}],
        },
        graph_expansion={
            "chunks": [
                {
                    "source": "src/PaymentController.java",
                    "score": 0.4,
                    "metadata": {
                        "record_id": "type:PaymentController",
                        "record_kind": "java_type",
                        "file": "src/PaymentController.java",
                        "expanded_from": "method:PaymentService.retryTimeout",
                        "relation_path": "calls_probable_target",
                        "source_manifest_hash": "mh-1",
                    },
                }
            ]
        },
        channel_config={"codecompass_fts": True, "codecompass_vector": True, "codecompass_graph": True},
        top_k=3,
        profile="balanced",
    )

    selected = list(payload["selected"])
    duplicate_targets = [item for item in selected if item["path"] == "src/PaymentService.java"]
    assert len(duplicate_targets) == 1
    contributions = dict(duplicate_targets[0]["channel_contributions"])
    assert "codecompass_fts" in contributions
    assert "codecompass_vector" in contributions
    assert payload["channel_diagnostics"]["codecompass_graph"]["reason"] == "expanded_from_seeds"
    graph_provenance = [item for item in payload["provenance"] if item["engine"] == "codecompass_graph"]
    assert graph_provenance
    assert graph_provenance[0]["expanded_from"] == "method:PaymentService.retryTimeout"
    assert graph_provenance[0]["relation_path"] == "calls_probable_target"
    assert payload["retrieval_trace"]["trace_id"].startswith("retrieval-")
    assert payload["retrieval_trace"]["context_hash"]
    assert payload["retrieval_trace"]["manifest_hash"] == "mh-1"
