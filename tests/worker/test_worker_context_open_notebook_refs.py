from agent.services.rag_service import RagService
from worker.core.context_bundle_adapter import ContextEnvelopeAdapter


def _bundle(llm_scope="local_only", *, chunk_llm_scope="local_only"):
    return {
        "llm_scope": llm_scope,
        "provenance_policy": {"visibility_level": "standard"},
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "open-notebook/survey.md",
                "content": "hub-centric orchestration keeps workers stateless " * 40,
                "score": 2.0,
                "metadata": {
                    "source_type": "open_notebook",
                    "source_id": "open-notebook-abc123def456",
                    "registry_source_id": "open-notebook-abc123def456",
                    "snapshot_id": "snap_1234567890abcdef",
                    "chunk_id": "onb:survey1",
                    "artifact_id": "art-1",
                    "record_kind": "primary_source",
                    "source_title": "Survey",
                    "content_hash": "hash-1",
                    "sanitized": {"llm_scope": chunk_llm_scope},
                },
            },
            {
                "engine": "repository_map",
                "source": "README.md",
                "content": "repo content",
                "score": 1.0,
                "metadata": {"source_type": "repo"},
            },
        ],
    }


def _service():
    return RagService(retrieval_service=object(), context_bundle_service=object())


def test_local_only_scope_produces_source_reference_refs():
    result = _service().build_open_notebook_worker_refs(_bundle("local_only"))
    assert result["llm_scope"] == "local_only"
    assert result["denied"] == []
    assert len(result["retrieval_refs"]) == 1
    ref = result["retrieval_refs"][0]
    assert ref["source_type"] == "open_notebook"
    assert ref["source_reference"]["snapshot_id"] == "snap_1234567890abcdef"
    assert ref["source_reference"]["schema"] == "source_reference.v1"
    # raw text is budgeted, not unlimited
    assert len(ref["content"]) <= 1200
    assert result["context_hash"]


def test_external_scope_denies_local_only_chunks():
    result = _service().build_open_notebook_worker_refs(
        _bundle("external_cloud_allowed", chunk_llm_scope="local_only")
    )
    assert result["retrieval_refs"] == []
    assert result["denied"]
    assert result["denied"][0]["reason_code"] == "denied_external_llm_scope"


def test_external_scope_allows_shared_chunks():
    result = _service().build_open_notebook_worker_refs(
        _bundle("external_cloud_allowed", chunk_llm_scope="shared_approved")
    )
    assert len(result["retrieval_refs"]) == 1
    assert result["denied"] == []


def test_context_hash_covers_source_snapshot_and_content_hash():
    base = _service().build_open_notebook_worker_refs(_bundle())
    changed = _bundle()
    changed["chunks"][0]["metadata"]["content_hash"] = "hash-2"
    other = _service().build_open_notebook_worker_refs(changed)
    assert base["context_hash"] != other["context_hash"]


def test_provenance_policy_is_forwarded():
    bundle = _bundle()
    bundle["provenance_policy"] = {"visibility_level": "admin"}
    result = _service().build_open_notebook_worker_refs(bundle)
    assert result["provenance_policy"]["visibility_level"] == "admin"


def test_worker_adapter_resolves_refs_and_blocks_missing_reference():
    result = _service().build_open_notebook_worker_refs(_bundle())
    refs = list(result["retrieval_refs"])
    refs.append({"source_type": "open_notebook", "origin_id": "rogue", "content": "raw text"})
    adapter = ContextEnvelopeAdapter()
    blocks, errors = adapter.resolve(
        {"context_bundle_id": "bundle-1", "context_hash": result["context_hash"], "retrieval_refs": refs}
    )
    assert len(blocks) == 1
    assert blocks[0].source_type == "open_notebook"
    assert any("open_notebook_ref_missing_source_reference" in error for error in errors)
