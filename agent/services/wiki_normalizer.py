from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from agent.services.wiki_chunking_policy import split_wiki_content
from agent.services.wiki_markup_cleaner import clean_wiki_markup
from agent.services.wiki_section_extractor import extract_wiki_sections
from agent.services.wiki_semantic_extractor import extract_wiki_semantic_signals


def article_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or "wiki-article"


class WikiRecordNormalizer:
    def __init__(self, *, allowed_namespaces: set[int] | None = None, max_chunk_chars: int = 700) -> None:
        self.allowed_namespaces = allowed_namespaces if allowed_namespaces is not None else {0}
        self.max_chunk_chars = max_chunk_chars

    def normalize_item(
        self,
        *,
        item: dict[str, Any],
        source_path: Path,
        source_id: str,
        ordinal: int,
        default_language: str,
        source_format: str = "xml",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "page":
            return self._normalize_page(
                item=item,
                source_path=source_path,
                source_id=source_id,
                ordinal=ordinal,
                default_language=default_language,
                source_format=source_format,
            )
        if kind == "doc":
            return self._normalize_doc(
                item=item,
                source_path=source_path,
                source_id=source_id,
                ordinal=ordinal,
                default_language=default_language,
                source_format=source_format,
            )
        return [], {"item": ordinal, "error": "unsupported_item_kind"}

    def _normalize_page(
        self,
        *,
        item: dict[str, Any],
        source_path: Path,
        source_id: str,
        ordinal: int,
        default_language: str,
        source_format: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        namespace = int(item.get("namespace") or 0)
        if namespace not in self.allowed_namespaces:
            return [], {"item": ordinal, "error": "namespace_filtered", "namespace": namespace}
        if bool(item.get("is_redirect")):
            return [], {"item": ordinal, "error": "redirect_filtered"}
        title = str(item.get("title") or "").strip()
        raw_text = str(item.get("text") or "").strip()
        if not title or not raw_text:
            return [], {"item": ordinal, "error": "missing_page_content"}
        semantics = extract_wiki_semantic_signals(raw_text)
        cleaned = clean_wiki_markup(raw_text)
        if not cleaned:
            return [], {"item": ordinal, "error": "empty_after_cleanup"}
        sections = extract_wiki_sections(text=cleaned, fallback_title="Overview")
        records: list[dict[str, Any]] = []
        for section in sections:
            records.extend(
                self._build_chunk_records(
                    article_title=title,
                    section_title=str(section.get("section_title") or "Overview"),
                    content=str(section.get("content") or ""),
                    source_path=source_path,
                    source_id=source_id,
                    ordinal=ordinal,
                    default_language=default_language,
                    source_format=source_format,
                    semantics=semantics,
                )
            )
        if not records:
            return [], {"item": ordinal, "error": "no_chunks"}
        return records, None

    def _normalize_doc(
        self,
        *,
        item: dict[str, Any],
        source_path: Path,
        source_id: str,
        ordinal: int,
        default_language: str,
        source_format: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        title = str(item.get("title") or "").strip()
        raw_text = str(item.get("text") or "").strip()
        if not title or not raw_text:
            return [], {"item": ordinal, "error": "missing_doc_content"}
        semantics = extract_wiki_semantic_signals(raw_text)
        cleaned = clean_wiki_markup(raw_text)
        if not cleaned:
            return [], {"item": ordinal, "error": "empty_after_cleanup"}
        records = self._build_chunk_records(
            article_title=title,
            section_title="Overview",
            content=cleaned,
            source_path=source_path,
            source_id=source_id,
            ordinal=ordinal,
            default_language=default_language,
            source_format=source_format,
            semantics=semantics,
        )
        if not records:
            return [], {"item": ordinal, "error": "no_chunks"}
        return records, None

    def _build_chunk_records(
        self,
        *,
        article_title: str,
        section_title: str,
        content: str,
        source_path: Path,
        source_id: str,
        ordinal: int,
        default_language: str,
        source_format: str,
        semantics: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        chunks = split_wiki_content(content, max_chars=self.max_chunk_chars)
        slug = article_slug(article_title)
        normalized: list[dict[str, Any]] = []
        for chunk_ordinal, chunk_text in enumerate(chunks, start=1):
            digest = hashlib.sha1(
                f"{source_id}|{article_title}|{section_title}|{chunk_text}".encode("utf-8")
            ).hexdigest()[:16]
            normalized.append(
                {
                    "kind": "wiki_section_chunk",
                    "id": f"{slug}:{ordinal}:{chunk_ordinal}",
                    "chunk_id": f"wiki:{digest}",
                    "chunk_ordinal": chunk_ordinal,
                    "file": source_path.name,
                    "article_title": article_title,
                    "wiki_article_id": slug,
                    "section_title": section_title,
                    "language": default_language,
                    "links": list(semantics.get("links") or []),
                    "categories": list(semantics.get("categories") or []),
                    "import_metadata": {
                        "source_scope": "wiki",
                        "source_id": source_id,
                        "source_line": ordinal,
                        "source_path": str(source_path),
                        "format": source_format,
                    },
                    "content": chunk_text,
                }
            )
        return normalized

