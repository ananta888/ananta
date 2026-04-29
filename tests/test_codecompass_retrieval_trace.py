from __future__ import annotations

from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.retrieval.retrieval_trace import build_retrieval_trace


def test_retrieval_trace_includes_channels_counts_hashes_and_latency():
    trace = build_retrieval_trace(
        query_original="retry timeout",
        query_rewritten="retry timeout payment service",
        channel_diagnostics={
            "codecompass_fts": {"status": "ready"},
            "codecompass_vector": {"status": "ready"},
            "codecompass_graph": {"status": "degraded"},
        },
        selected=[
            {"channel": "codecompass_fts"},
            {"channel": "codecompass_vector"},
        ],
        provenance=[
            {"record_id": "method:PaymentService.retryTimeout"},
            {"record_id": "type:PaymentController"},
        ],
        manifest_hash="mh-1",
        graph_seed_count=2,
        graph_expanded_count=1,
        channel_latency_ms={"codecompass_fts": 8, "codecompass_vector": 14, "codecompass_graph": 4},
    )

    assert trace["trace_id"].startswith("retrieval-")
    assert set(trace["enabled_channels"]) == {"codecompass_fts", "codecompass_vector"}
    assert trace["degraded_channels"] == ["codecompass_graph"]
    assert trace["seed_counts"]["graph_seed_count"] == 2
    assert trace["graph_expansion_counts"]["expanded_nodes"] == 1
    assert trace["final_chunk_count"] == 2
    assert trace["context_hash"]
    assert trace["manifest_hash"] == "mh-1"
    assert trace["channel_latency_ms"]["codecompass_vector"] == 14


def test_task_scoped_execution_extracts_retrieval_trace_link():
    link = TaskScopedExecutionService._extract_retrieval_trace_link(
        {
            "bundle_metadata": {
                "retrieval_trace": {
                    "trace_id": "retrieval-abc123",
                    "context_hash": "ctx-1",
                    "manifest_hash": "mh-1",
                }
            }
        }
    )

    assert link["retrieval_trace_id"] == "retrieval-abc123"
    assert link["retrieval_context_hash"] == "ctx-1"
    assert link["retrieval_manifest_hash"] == "mh-1"

