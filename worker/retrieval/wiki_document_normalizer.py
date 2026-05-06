from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_wiki_records_for_retrieval(*, records: list[dict[str, Any]], source_id: str, source_format: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(list(records or []), start=1):
        if not isinstance(record, dict):
            continue
        article_title = str(record.get("article_title") or "").strip()
        if not article_title:
            continue
        section_title = str(record.get("section_title") or "Overview").strip() or "Overview"
        content = str(record.get("content") or "").strip()
        digest = hashlib.sha1(
            json.dumps(
                {
                    "source_id": source_id,
                    "source_format": source_format,
                    "article_title": article_title,
                    "section_title": section_title,
                    "content": content,
                    "index": index,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16]
        normalized.append(
            {
                "schema": "wiki_codecompass_record.v1",
                "kind": "chunk",
                "record_id": str(record.get("chunk_id") or f"wiki:{digest}"),
                "article_title": article_title,
                "section_title": section_title,
                "content": content,
                "source": {
                    "source_id": source_id,
                    "format": source_format,
                    "revision": str(record.get("revision") or record.get("import_revision") or "").strip() or None,
                    "namespace": int(record.get("namespace")) if str(record.get("namespace") or "").strip().isdigit() else None,
                },
                "metadata": {
                    "wiki_article_id": str(record.get("wiki_article_id") or "").strip() or None,
                    "chunk_ordinal": int(record.get("chunk_ordinal") or 0),
                    "links": list(record.get("links") or []),
                    "categories": list(record.get("categories") or []),
                    "import_metadata": dict(record.get("import_metadata") or {}),
                },
            }
        )
    return normalized

