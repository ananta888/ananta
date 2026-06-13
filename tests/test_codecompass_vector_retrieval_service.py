from __future__ import annotations

import json
from pathlib import Path

from agent.services.codecompass_vector_retrieval_service import CodeCompassVectorRetrievalService


def _write_codecompass_fixture(root: Path) -> None:
    out = root / "rag-helper" / "out"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_hash": "mh-fixture",
                "profile_name": "python",
                "source_scope": "repo",
                "retrieval_cache_state": "cache-fixture",
            }
        ),
        encoding="utf-8",
    )
    (out / "embedding.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "emb-payment",
                        "kind": "python_function",
                        "file": "src/payment.py",
                        "embedding_text": "payment retry timeout service",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-payment"},
                    },
                    {
                        "id": "emb-invoice",
                        "kind": "python_function",
                        "file": "src/invoice.py",
                        "embedding_text": "invoice tax calculation",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-invoice"},
                    },
                    {
                        "id": "emb-doc",
                        "kind": "markdown_doc",
                        "file": "docs/retrieval.md",
                        "embedding_text": "retrieval architecture notes",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-doc"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_codecompass_vector_retrieval_service_indexes_and_searches_without_network(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        provider_config={"provider": "local_hash", "model_version": "hash-v1", "dimensions": 12},
    )

    rows = service.search(query="payment timeout", top_k=2)
    diagnostic = service.last_diagnostic()

    assert rows
    assert rows[0]["engine"] == "codecompass_vector"
    assert rows[0]["metadata"]["record_id"]
    assert "vector_score" in rows[0]["metadata"]
    assert diagnostic["status"] == "ready"
    assert diagnostic["refresh"]["manifest_hash"] == "mh-fixture"


def test_codecompass_vector_retrieval_service_missing_embedding_degrades_empty(tmp_path: Path) -> None:
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
    )

    assert service.search(query="anything") == []
    assert service.last_diagnostic()["status"] == "degraded"
    assert service.last_diagnostic()["reason"] == "missing_embedding_records"


def test_codecompass_vector_retrieval_service_applies_allowed_paths(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
    )

    rows = service.search(query="payment timeout retrieval", top_k=5, allowed_paths=["src"])

    assert rows
    assert all(str(row["source"]).startswith("src/") for row in rows)
