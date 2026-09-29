from __future__ import annotations

import os

import pytest

from agent.services.source_catalog_service import SourceCatalogService, validate_source_catalog_payload


def _authorized_source_ids(count: int) -> list[str]:
    values = [
        item.strip() for item in os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_IDS", "").split(",") if item.strip()
    ]
    if len(values) < count:
        pytest.skip("authoritative_source_evidence_unavailable")
    return values[:count]


def _payload(order: int = 0) -> dict:
    selected = [
        {
            "path": "src/b.py",
            "content_hash": "hash-b-1234",
            "channel": "codecompass_fts",
            "metadata": {
                "record_kind": "repo_file",
                "record_id": "rid-b",
                "source_manifest_hash": "m1",
                "line_start": 3,
                "line_end": 5,
            },
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
    source_id = _authorized_source_ids(1)[0]
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
                "source_id": source_id,
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
                "source_id": source_id,
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


def test_source_catalog_v2_accepts_only_provider_issued_source_refs() -> None:
    source_ids = _authorized_source_ids(2)
    payload = _payload(0)
    payload["retrieval_trace"].update(
        {
            "tenant_id": "tenant-a",
            "scope": "repo",
        }
    )
    for index, item in enumerate(payload["selected"], start=1):
        item.update(
            {
                "source_id": source_ids[index - 1],
                "source_version": "snapshot-1",
                "tenant_id": "tenant-a",
                "scope": "repo",
                "provenance": {
                    "source_id": source_ids[index - 1],
                    "source_version": "snapshot-1",
                    "provider": "test",
                },
            }
        )
    # Provenance rows intentionally lack an authoritative source id and stay as
    # content-free rejection stubs instead of receiving generated SRC numbers.
    catalog = SourceCatalogService().build_catalog(task_id="t-1", retrieval_payload=payload)

    assert catalog["schema"] == "source_catalog.v2"
    assert {item["source_id"] for item in catalog["sources"]} == set(source_ids)
    assert catalog["rejected_candidates"][0]["reason_code"] == "source_id_missing"
    assert not validate_source_catalog_payload(catalog)


def test_source_catalog_v2_never_synthesizes_source_ids_from_paths() -> None:
    catalog = SourceCatalogService().build_catalog(task_id="t-1", retrieval_payload=_payload(0))

    assert catalog["sources"] == []
    assert {item["reason_code"] for item in catalog["rejected_candidates"]} == {"source_id_missing"}
    assert catalog["catalog_state"] == "degraded"
