from __future__ import annotations


def build_embedding_records(index_records: list[dict]) -> list[dict]:
    embedding_records: list[dict] = []
    for record in index_records:
        embedding_records.append({
            "id": record.get("id"),
            "kind": record.get("kind"),
            "file": record.get("file"),
            "embedding_text": record.get("embedding_text", ""),
            "summary": record.get("summary"),
            "role_labels": record.get("role_labels"),
            "importance_score": record.get("importance_score"),
            "generated_code": record.get("generated_code", False),
            "generated_code_reasons": record.get("generated_code_reasons", []),
        })
    return embedding_records


def build_context_records(detail_records: list[dict]) -> list[dict]:
    context_records: list[dict] = []
    for record in detail_records:
        payload = dict(record)
        payload.pop("embedding_text", None)
        context_records.append(payload)
    return context_records
