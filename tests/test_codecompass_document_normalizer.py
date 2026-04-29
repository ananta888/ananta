from __future__ import annotations

from worker.retrieval.codecompass_document_normalizer import normalize_codecompass_records


def test_codecompass_document_normalizer_preserves_fields_and_hashes():
    records = [
        {
            "id": "node-1",
            "kind": "java_type",
            "file": "src/PaymentService.java",
            "parent_id": "file-1",
            "role_labels": ["service"],
            "importance_score": 3.2,
            "generated_code": False,
            "symbols": ["PaymentService"],
            "summary": "Service class",
            "content": "public class PaymentService {}",
            "relations": ["implements PaymentApi"],
            "focus_terms": ["payment", "service"],
        }
    ]
    docs = normalize_codecompass_records(records=records, manifest_hash="mh-1")
    assert len(docs) == 1
    assert docs[0]["record_id"] == "node-1"
    assert docs[0]["kind"] == "java_type"
    assert docs[0]["text_fields"]["symbol_text"] == "PaymentService"
    assert docs[0]["text_fields"]["path_text"] == "src/PaymentService.java"
    assert docs[0]["text_fields"]["focus_text"]
    assert docs[0]["document_hash"]


def test_codecompass_document_normalizer_hash_is_deterministic():
    records = [{"id": "same", "kind": "xml_tag", "file": "application.xml", "content": "<bean id='x'/>"}]
    one = normalize_codecompass_records(records=records, manifest_hash="mh-1")
    two = normalize_codecompass_records(records=records, manifest_hash="mh-1")
    assert one[0]["document_hash"] == two[0]["document_hash"]

