from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


def compact_wiki_jsonl(
    *,
    source_path: Path,
    dest_path: Path,
    max_chunks_per_article: int = 3,
    min_content_chars: int = 200,
    progress_callback=None,
) -> dict[str, int]:
    """Stream-filter a JSONL file to a compact version.

    Keeps the first max_chunks_per_article chunks per article that have
    at least min_content_chars of content. Streams line-by-line — safe
    for files of any size.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    per_article: dict[str, int] = defaultdict(int)
    total = kept = 0

    with source_path.open("r", encoding="utf-8") as src, \
         dest_path.open("w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = str(record.get("article_title") or "")
            content = str(record.get("content") or "")
            if len(content) < min_content_chars:
                continue
            if per_article[title] >= max_chunks_per_article:
                continue
            per_article[title] += 1
            dst.write(line + "\n")
            kept += 1
            if progress_callback and total % 50_000 == 0:
                progress_callback(total, kept)

    logger.info(
        "compact_wiki_jsonl: %d → %d records (%d articles, %.1f%%)",
        total, kept, len(per_article), 100 * kept / total if total else 0,
    )
    return {"total": total, "kept": kept, "articles": len(per_article)}

