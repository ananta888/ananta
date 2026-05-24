from __future__ import annotations

from agent.services.source_catalog_service import SourceCatalogService, validate_source_catalog_payload


def _payload(order: int = 0) -> dict:
    selected = [
        {
            "path": "src/b.py",
            "content_hash": "hash-b-1234",
            "channel": "codecompass_fts",
            "metadata": {"record_kind": "repo_file", "record_id": "rid-b", "source_manifest_hash": "m1", "line_start": 3, "line_end": 5},
        },
        {
            "path": "docs/a.md",
            "content_hash": "hash-a-1234",
            "channel": "wiki",
            "metadata": {"record_kind": "wiki_chunk", "record_id": "rid-a", "source_manifest_hash": "m1"},
        },
    ]
    if order:
        selected = list(reversed(selected))
    return {
        "selected": selected,
        "provenance": [
            {
                "engine": "codecompass_fts",
                "record_id": "rid-b",
                "file": "src/b.py",
                "kind": "repo_file",
                "score": 0.8,
                "manifest_hash": "m1",
                "line_start": 3,
                "line_end": 5,
            }
        ],
        "retrieval_trace": {"trace_id": "retrieval-t1", "context_hash": "ctx-12345", "manifest_hash": "m1"},
    }


def test_source_catalog_is_deterministic_for_reordered_input() -> None:
    svc = SourceCatalogService()
    cat1 = svc.build_catalog(task_id="t-1", retrieval_payload=_payload(0))
    cat2 = svc.build_catalog(task_id="t-1", retrieval_payload=_payload(1))

    assert [s["source_id"] for s in cat1["sources"]] == [s["source_id"] for s in cat2["sources"]]
    assert cat1["catalog_hash"] == cat2["catalog_hash"]


def test_source_catalog_hash_changes_with_record_change() -> None:
    svc = SourceCatalogService()
    p1 = _payload(0)
    p2 = _payload(0)
    p2["selected"][0]["metadata"]["record_id"] = "rid-b-2"
    cat1 = svc.build_catalog(task_id="t-1", retrieval_payload=p1)
    cat2 = svc.build_catalog(task_id="t-1", retrieval_payload=p2)

    assert cat1["catalog_hash"] != cat2["catalog_hash"]


def test_source_catalog_duplicate_source_id_rejected_by_validator() -> None:
    payload = {
        "schema": "source_catalog.v1",
        "catalog_id": "catalog-1",
        "task_id": "t-1",
        "retrieval_trace_id": "rt-1",
        "retrieval_context_hash": "ctx-1",
        "retrieval_manifest_hash": "mh-1",
        "catalog_hash": "0123456789abcdef",
        "sources": [
            {
                "source_id": "SRC_0001",
                "source_type": "rag_chunk",
                "path": "a",
                "record_id": "r1",
                "line_start": None,
                "line_end": None,
                "content_hash": "aaaaaaaa",
                "manifest_hash": "m1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "created_at": 1.0,
                "task_id": "t-1",
            },
            {
                "source_id": "SRC_0001",
                "source_type": "repo_file",
                "path": "b",
                "record_id": "r2",
                "line_start": None,
                "line_end": None,
                "content_hash": "bbbbbbbb",
                "manifest_hash": "m1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "created_at": 2.0,
                "task_id": "t-1",
            },
        ],
    }
    errors = validate_source_catalog_payload(payload)
    assert any("duplicate source_id" in e for e in errors)
