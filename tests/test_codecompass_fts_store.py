from __future__ import annotations

from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore


def test_codecompass_fts_store_supports_rebuild_and_exact_boost(tmp_path):
    store = CodeCompassFtsStore(db_path=tmp_path / "cc_fts.sqlite")
    diagnostics = store.diagnostics()
    assert diagnostics["status"] in {"ready", "degraded"}
    if diagnostics["status"] != "ready":
        return
    docs = [
        {
            "record_id": "r1",
            "kind": "java_type",
            "file": "src/PaymentService.java",
            "parent_id": "",
            "role_labels": ["service"],
            "importance_score": 3.0,
            "generated_code": False,
            "manifest_hash": "mh-1",
            "document_hash": "dh-1",
            "text_fields": {
                "symbol_text": "PaymentService",
                "path_text": "src/PaymentService.java",
                "kind_text": "java_type",
                "summary_text": "payment service",
                "content_text": "class PaymentService handles timeout",
                "relation_text": "",
                "focus_text": "timeout",
            },
        },
        {
            "record_id": "r2",
            "kind": "md_section",
            "file": "docs/payment.md",
            "parent_id": "",
            "role_labels": [],
            "importance_score": 1.0,
            "generated_code": False,
            "manifest_hash": "mh-1",
            "document_hash": "dh-2",
            "text_fields": {
                "symbol_text": "",
                "path_text": "docs/payment.md",
                "kind_text": "md_section",
                "summary_text": "timeout notes",
                "content_text": "payment timeout troubleshooting",
                "relation_text": "",
                "focus_text": "",
            },
        },
    ]
    store.rebuild(documents=docs, retrieval_cache_state="state-1")
    rows = store.search(query="PaymentService timeout", top_k=2)
    assert len(rows) == 2
    assert rows[0]["record_id"] == "r1"
    assert rows[0]["boost_breakdown"]["exact_symbol_or_path_hit"] is True

