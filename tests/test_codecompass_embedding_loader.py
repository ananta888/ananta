from __future__ import annotations

from worker.retrieval.codecompass_embedding_loader import load_codecompass_embedding_documents


def test_codecompass_embedding_loader_uses_embedding_text_and_preserves_metadata():
    manifest = {
        "manifest_hash": "mh-1",
        "source_scope": "repo",
        "profile_name": "java_spring",
    }
    payload = load_codecompass_embedding_documents(
        records=[
            {
                "id": "emb-1",
                "kind": "java_method",
                "file": "src/PaymentService.java",
                "parent_id": "type-1",
                "role_labels": ["service"],
                "importance_score": 2.0,
                "embedding_text": "retry timeout payment service method",
                "_provenance": {"output_kind": "embedding", "record_id": "emb-1"},
            },
            {
                "id": "emb-2",
                "kind": "java_method",
                "file": "src/PaymentService.java",
                "embedding_text": "",
                "_provenance": {"output_kind": "embedding", "record_id": "emb-2"},
            },
            {
                "id": "det-1",
                "kind": "java_method",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "details", "record_id": "det-1"},
            },
        ],
        manifest=manifest,
    )

    documents = payload["documents"]
    diagnostics = payload["diagnostics"]
    assert len(documents) == 1
    assert documents[0]["record_id"] == "emb-1"
    assert documents[0]["embedding_text"] == "retry timeout payment service method"
    assert documents[0]["kind"] == "java_method"
    assert documents[0]["file"] == "src/PaymentService.java"
    assert documents[0]["parent_id"] == "type-1"
    assert documents[0]["role_labels"] == ["service"]
    assert documents[0]["importance_score"] == 2.0
    assert documents[0]["source_scope"] == "repo"
    assert documents[0]["profile_name"] == "java_spring"
    assert diagnostics["candidate_embedding_records"] == 2
    assert diagnostics["skipped_missing_embedding_text"] == 1
    assert diagnostics["skipped_non_embedding_records"] == 1

