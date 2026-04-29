from __future__ import annotations

from worker.retrieval.codecompass_fts_engine import CodeCompassFtsEngine
from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore


def test_codecompass_fts_engine_returns_contextchunk_compatible_records(tmp_path):
    store = CodeCompassFtsStore(db_path=tmp_path / "cc_fts.sqlite")
    if store.diagnostics()["status"] != "ready":
        return
    store.rebuild(
        documents=[
            {
                "record_id": "r1",
                "kind": "java_method",
                "file": "src/PaymentService.java",
                "parent_id": "t1",
                "role_labels": ["service"],
                "importance_score": 3.0,
                "generated_code": False,
                "manifest_hash": "mh",
                "document_hash": "dh",
                "text_fields": {
                    "symbol_text": "retryTimeout",
                    "path_text": "src/PaymentService.java",
                    "kind_text": "java_method",
                    "summary_text": "retry timeout",
                    "content_text": "void retryTimeout() {}",
                    "relation_text": "calls PaymentRepository",
                    "focus_text": "payment timeout",
                },
            }
        ],
        retrieval_cache_state="cache-state-1",
    )
    engine = CodeCompassFtsEngine(store=store)
    chunks = engine.search(query="retryTimeout PaymentService", top_k=3, task_kind="bugfix", retrieval_intent="exact_symbol")
    assert chunks
    assert chunks[0]["engine"] == "codecompass_fts"
    assert chunks[0]["metadata"]["bm25_score"] >= 0.0
    assert "field_boost_breakdown" in chunks[0]["metadata"]

