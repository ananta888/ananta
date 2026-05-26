from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from agent.services.wiki_chunking_policy import split_wiki_content
from agent.services.wiki_mediawiki_xml_parser import MediaWikiXmlDumpParser


def _is_disambiguation(title: str, text: str) -> bool:
    joined = f"{title}\n{text}".lower()
    return "(begriffsklärung)" in joined or "disambiguation" in joined


def _is_stub(text: str) -> bool:
    return len(str(text or "").split()) < 60


def _clean_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def ingest_wikipedia_dump(
    *,
    corpus_path: Path,
    source_id: str,
    snapshot_id: str,
    citation_source: dict[str, Any],
    parser: MediaWikiXmlDumpParser | None = None,
    index_path: Path | None = None,
    max_chunk_chars: int = 1200,
) -> dict[str, Any]:
    parser_obj = parser or MediaWikiXmlDumpParser()
    chunks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_content: set[str] = set()
    page_count = 0
    for item in parser_obj.iter_items(corpus_path=corpus_path, index_path=index_path):
        try:
            title = str(item.get("title") or "").strip()
            if not title:
                issues.append({"reason_code": "missing_title"})
                continue
            page_count += 1
            if bool(item.get("is_redirect")):
                issues.append({"reason_code": "redirect", "title": title})
                continue
            raw_text = _clean_text(str(item.get("text") or ""))
            if not raw_text:
                issues.append({"reason_code": "empty_text", "title": title})
                continue
            flags = {
                "is_disambiguation": _is_disambiguation(title, raw_text),
                "is_stub": _is_stub(raw_text),
            }
            for ordinal, chunk_text in enumerate(split_wiki_content(raw_text, max_chars=max_chunk_chars), start=1):
                digest = hashlib.sha256(f"{title}|{chunk_text}".encode("utf-8")).hexdigest()
                if digest in seen_content:
                    continue
                seen_content.add(digest)
                chunk_id = f"wiki:{digest[:16]}"
                canonical_url = f"https://de.wikipedia.org/wiki/{title.replace(' ', '_')}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "article_title": title,
                        "page_id": str(item.get("page_id") or ""),
                        "revision": str(item.get("revision") or item.get("timestamp") or ""),
                        "section_title": f"chunk-{ordinal}",
                        "canonical_url": canonical_url,
                        "content": chunk_text,
                        "source_reference": {
                            "schema": "source_reference.v1",
                            "source_id": source_id,
                            "snapshot_id": snapshot_id,
                            "chunk_id": chunk_id,
                            "canonical_url": canonical_url,
                            "title": title,
                            "license_ref": str(citation_source.get("license_ref") or "CC BY-SA"),
                            "retrieved_at": str(citation_source.get("retrieved_at") or ""),
                            "attribution_text": str(
                                citation_source.get("citation_text")
                                or f"Wikipedia/Wikimedia, {title}, dump snapshot {snapshot_id}"
                            ),
                        },
                        "attribution_text": str(
                            citation_source.get("citation_text")
                            or f"Wikipedia/Wikimedia, {title}, dump snapshot {snapshot_id}"
                        ),
                        "license_ref": str(citation_source.get("license_ref") or "CC BY-SA"),
                        "flags": flags,
                    }
                )
        except Exception as exc:
            issues.append({"reason_code": "item_parse_failed", "human_message": str(exc)})
            continue
    return {
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "chunk_count": len(chunks),
        "page_count": page_count,
        "chunks": chunks,
        "issues": issues,
    }

