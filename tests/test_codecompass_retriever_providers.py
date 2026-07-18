from __future__ import annotations

import hashlib
import json
import os

import pytest

from ananta_contracts.retrieval import RetrievalRequest, SourceRef
from worker.retrieval.codecompass_channel_providers import JsonlSymbolProvider
from worker.retrieval.codecompass_retriever import CodeCompassRetriever


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _authorized_source_id() -> str:
    source_id = os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID", "").strip()
    if not source_id:
        pytest.skip("authoritative_source_evidence_unavailable")
    return source_id


def test_worker_retriever_queries_real_symbol_provider_and_enforces_source_ref(tmp_path) -> None:
    source_id = _authorized_source_id()
    provenance = {
        "source_id": source_id,
        "source_version": "snapshot-1",
        "provider": "rag-helper",
    }
    details = tmp_path / "details.jsonl"
    details.write_text(
        json.dumps(
            {
                "id": "sym-1",
                "kind": "python_function",
                "name": "runtime_dispatch",
                "file": "agent/runtime.py",
                "summary": "runtime dispatch evidence",
                "content_hash": "content-123",
                "source_id": source_id,
                "source_version": "snapshot-1",
                "tenant_id": "tenant-1",
                "scope": "worker_retrieval",
                "manifest_hash": "manifest-1",
                "provenance": provenance,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_ref = SourceRef(
        source_id=source_id,
        source_version="snapshot-1",
        tenant_id="tenant-1",
        scope="worker_retrieval",
        provenance_digest=_digest(provenance),
    )
    retriever = CodeCompassRetriever(
        channel_providers={"symbol": JsonlSymbolProvider(paths=[details])},
    )

    result = retriever.retrieve(
        RetrievalRequest(
            query="runtime_dispatch",
            tenant_id="tenant-1",
            scope="worker_retrieval",
            allowed_source_ids=frozenset({source_id}),
            allowed_source_refs=(source_ref,),
            repository_revision="snapshot-1",
            manifest_hash="manifest-1",
            source_allowlist_version="allowlist-1",
        )
    )

    assert result.metadata["queried_channels"] == ["symbol"]
    assert result.metadata["consistency_state"] == "current"
    assert result.sources[0].path == "agent/runtime.py"
    assert result.sources[0].source_ref == source_ref


def test_worker_retriever_rejects_stale_manifest(tmp_path) -> None:
    source_id = _authorized_source_id()
    provenance = {"source_id": source_id, "source_version": "snapshot-1"}
    details = tmp_path / "details.jsonl"
    details.write_text(
        json.dumps(
            {
                "id": "sym-1",
                "name": "runtime_dispatch",
                "file": "agent/runtime.py",
                "summary": "runtime dispatch evidence",
                "source_id": source_id,
                "source_version": "snapshot-1",
                "tenant_id": "tenant-1",
                "scope": "worker_retrieval",
                "manifest_hash": "stale-manifest",
                "provenance": provenance,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_ref = SourceRef(
        source_id=source_id,
        source_version="snapshot-1",
        tenant_id="tenant-1",
        scope="worker_retrieval",
        provenance_digest=_digest(provenance),
    )
    result = CodeCompassRetriever(
        channel_providers={"symbol": JsonlSymbolProvider(paths=[details])},
    ).retrieve(
        RetrievalRequest(
            query="runtime_dispatch",
            tenant_id="tenant-1",
            scope="worker_retrieval",
            allowed_source_ids=frozenset({source_id}),
            allowed_source_refs=(source_ref,),
            repository_revision="snapshot-1",
            manifest_hash="current-manifest",
            source_allowlist_version="allowlist-1",
        )
    )
    assert result.sources == ()
    assert result.metadata["consistency_state"] == "degraded"
    assert "source_manifest_mismatch" in result.rejection_reasons


def test_worker_retriever_without_mounted_or_injected_provider_is_explicitly_degraded(monkeypatch) -> None:
    for name in (
        "ANANTA_CODECOMPASS_FTS_DB",
        "ANANTA_CODECOMPASS_VECTOR_INDEX",
        "ANANTA_CODECOMPASS_SYMBOL_PATHS",
        "ANANTA_CODECOMPASS_GRAPH_INDEX",
    ):
        monkeypatch.delenv(name, raising=False)
    result = CodeCompassRetriever().retrieve(
        RetrievalRequest(
            query="runtime",
            tenant_id="tenant-1",
            scope="worker_retrieval",
            allowed_source_ids=frozenset(),
        )
    )

    assert result.sources == ()
    assert result.metadata["consistency_state"] == "degraded"
    assert result.rejection_reasons == ("retrieval_provider_unconfigured",)


def test_worker_retriever_rejects_an_empty_configured_production_channel(tmp_path) -> None:
    empty_details = tmp_path / "details.jsonl"
    empty_details.write_text("", encoding="utf-8")

    result = CodeCompassRetriever(
        channel_providers={"symbol": JsonlSymbolProvider(paths=[empty_details])},
    ).retrieve(
        RetrievalRequest(
            query="runtime_dispatch",
            tenant_id="tenant-1",
            scope="worker_retrieval",
            allowed_source_ids=frozenset(),
        )
    )

    assert result.sources == ()
    assert result.metadata["consistency_state"] == "degraded"
    assert result.rejection_reasons == ("production_channel_empty",)
