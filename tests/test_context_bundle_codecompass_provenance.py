from agent.services.context_bundle_service import ContextBundleService


def test_context_bundle_preserves_codecompass_trace_and_provenance_shape():
    service = ContextBundleService()
    bundle = service.build_bundle(
        query="secure timeout retry flow",
        context_payload={
            "chunks": [
                {
                    "engine": "knowledge_index",
                    "source": "artifact:java-service",
                    "score": 0.98,
                    "content": "RetryPolicy links to TimeoutConfig.",
                    "metadata": {
                        "record_kind": "graph_expansion",
                        "source_type": "artifact",
                        "source_id": "artifact-1",
                        "chunk_id": "artifact:graph-node-2",
                        "expanded_from": "artifact:graph-node-1",
                        "relation_path": "class:RetryPolicy->uses->class:TimeoutConfig",
                        "source_manifest_hash": "manifest-abc",
                        "collection_names": ["codecompass"],
                    },
                }
            ],
            "strategy": {"fusion": {"candidate_counts": {"all": 1, "final": 1}}},
            "retrieval_trace": {
                "trace_id": "retrieval-test-1234",
                "enabled_channels": ["knowledge_index", "repository_map"],
                "degraded_channels": ["repository_map"],
                "seed_counts": {"graph_seed_count": 1},
                "graph_expansion_counts": {"expanded_nodes": 1},
                "final_chunk_count": 1,
                "context_hash": "ctx-hash-1",
                "manifest_hash": "manifest-abc",
                "selected_chunk_counts_by_channel": {"knowledge_index": 1},
                "channel_latency_ms": {"knowledge_index": 5},
            },
        },
        include_context_text=False,
        policy_mode="standard",
        task_kind="bugfix",
        total_budget_tokens=4096,
        budget_tokens_by_mode={"compact": 4096, "standard": 4096, "full": 4096},
    )

    assert bundle["retrieval_trace"]["trace_id"] == "retrieval-test-1234"
    assert bundle["selection_trace"]["retrieval_trace_id"] == "retrieval-test-1234"
    assert bundle["selection_trace"]["context_hash"] == "ctx-hash-1"
    assert bundle["selection_trace"]["manifest_hash"] == "manifest-abc"
    assert bundle["explainability"]["channel_contributions"] == {"knowledge_index": 1}
    source = bundle["explainability"]["sources"][0]
    assert source["expanded_from"] == "artifact:graph-node-1"
    assert source["relation_path"] == "class:RetryPolicy->uses->class:TimeoutConfig"
    assert source["source_manifest_hash"] == "manifest-abc"


def test_context_bundle_synthesizes_trace_when_retrieval_trace_missing():
    service = ContextBundleService()
    bundle = service.build_bundle(
        query="retry timeout",
        context_payload={
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "src/service.py",
                    "score": 0.7,
                    "content": "timeout and retry handling",
                    "metadata": {
                        "record_kind": "code",
                        "source_type": "repo",
                        "chunk_id": "repo:abc",
                        "source_manifest_hash": "manifest-xyz",
                    },
                }
            ],
            "strategy": {"fusion": {"candidate_counts": {"all": 1, "final": 1}}},
        },
        include_context_text=False,
        policy_mode="standard",
        task_kind="coding",
        total_budget_tokens=4096,
        budget_tokens_by_mode={"compact": 4096, "standard": 4096, "full": 4096},
    )

    trace = bundle["retrieval_trace"]
    assert trace["trace_id"].startswith("retrieval-")
    assert trace["manifest_hash"] == "manifest-xyz"
    assert trace["final_chunk_count"] == 1
    assert trace["selected_chunk_counts_by_channel"] == {"repository_map": 1}
    assert bundle["selection_trace"]["context_hash"] == trace["context_hash"]
