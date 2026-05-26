from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse


def _strip_html(value: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", str(value or ""), flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_with_budget(text: str, *, target_words: int = 1000, min_words: int = 800, max_words: int = 1600) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    chunks: list[list[str]] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(current) >= target_words and len(current) >= min_words:
            chunks.append(current)
            current = []
    if current:
        if chunks and len(current) < min_words and len(chunks[-1]) + len(current) <= max_words:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    return [" ".join(chunk) for chunk in chunks if chunk]


def _classify_page(url: str) -> str:
    lowered = str(url or "").lower()
    if "/guides" in lowered or "/documentation" in lowered:
        return "content"
    if lowered.endswith("/") or lowered.endswith("/documentation"):
        return "navigation"
    return "content"


def ingest_keycloak_pages(
    *,
    source_id: str,
    snapshot_id: str,
    citation_source: dict[str, Any],
    pages: list[dict[str, Any]],
    chunk_words_target: int = 1000,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for page in pages:
        url = str(page.get("url") or "").strip()
        doc_title = urlparse(url).path.strip("/") or "Keycloak Documentation"
        page_text = _strip_html(str(page.get("raw_html") or page.get("extracted_text") or ""))
        classification = _classify_page(url)
        for index, chunk_text in enumerate(
            _split_with_budget(page_text, target_words=chunk_words_target, min_words=800, max_words=1600),
            start=1,
        ):
            digest = hashlib.sha256(f"{url}|{chunk_text}".encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            chunk_id = f"keycloak:{digest[:16]}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "doc_title": doc_title,
                    "section_title": f"chunk-{index}",
                    "canonical_url": url,
                    "content": chunk_text,
                    "classification": classification,
                    "source_reference": {
                        "schema": "source_reference.v1",
                        "source_id": source_id,
                        "snapshot_id": snapshot_id,
                        "chunk_id": chunk_id,
                        "canonical_url": url,
                        "title": doc_title,
                        "license_ref": str(citation_source.get("license_ref") or "unknown"),
                        "retrieved_at": str(citation_source.get("retrieved_at") or ""),
                        "attribution_text": str(citation_source.get("citation_text") or ""),
                    },
                }
            )
    return chunks

