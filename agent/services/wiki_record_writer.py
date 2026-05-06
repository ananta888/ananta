from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def sort_wiki_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        list(records or []),
        key=lambda item: (
            str(item.get("article_title") or "").lower(),
            str(item.get("section_title") or "").lower(),
            str(item.get("file") or "").lower(),
            int(item.get("chunk_ordinal") or 0),
            str(item.get("chunk_id") or ""),
        ),
    )


def write_wiki_jsonl_cache(*, records: list[dict[str, Any]], cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
    cache_path.write_text((payload + "\n") if payload else "", encoding="utf-8")
    return cache_path

