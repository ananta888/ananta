"""Deterministic import of line-delimited JSON wiki corpora into retrieval chunks."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from agent.services.wiki_chunking_policy import split_wiki_content

logger = logging.getLogger(__name__)


class WikiJsonlImporter:
    """Validate, normalize and deterministically order the records of one JSONL wiki corpus."""

    def import_file(
        self,
        *,
        corpus_path: str,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
    ) -> dict[str, object]:
        path = Path(str(corpus_path or "").strip()).expanduser().resolve()
        if not path.exists():
            raise ValueError("wiki_corpus_not_found")
        if not path.is_file():
            raise ValueError("wiki_corpus_not_file")
        normalized_source_id = str(source_id or "").strip() or path.stem
        lines = path.read_text(encoding="utf-8").splitlines()
        records: list[dict] = []
        issues: list[dict] = []
        for line_number, raw_line in enumerate(lines, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                issue = {"line": line_number, "error": "invalid_json", "details": str(exc)}
                logger.warning("Wiki import skipped malformed JSON line", extra=issue)
                issues.append(issue)
                if strict:
                    raise ValueError("wiki_corpus_invalid_json") from exc
                continue
            if not isinstance(payload, dict):
                issue = {"line": line_number, "error": "record_not_object"}
                logger.warning("Wiki import skipped non-object record", extra=issue)
                issues.append(issue)
                if strict:
                    raise ValueError("wiki_corpus_invalid_record")
                continue
            try:
                records.extend(
                    self.normalize_record(
                        payload,
                        source_path=path,
                        source_id=normalized_source_id,
                        line_number=line_number,
                        default_language=default_language,
                        source_format="jsonl",
                    )
                )
            except ValueError as exc:
                issue = {"line": line_number, "error": str(exc)}
                logger.warning("Wiki import skipped invalid record", extra=issue)
                issues.append(issue)
                if strict:
                    raise ValueError("wiki_corpus_invalid_record") from exc
        records = sorted(
            records,
            key=lambda item: (
                str(item.get("article_title") or "").lower(),
                str(item.get("section_title") or "").lower(),
                str(item.get("file") or "").lower(),
                int(item.get("chunk_ordinal") or 0),
            ),
        )
        if not records:
            raise ValueError("wiki_corpus_no_valid_records")
        return {
            "source_scope": "wiki",
            "source_id": normalized_source_id,
            "corpus_path": str(path),
            "records": records,
            "issues": issues,
            "stats": {
                "input_lines": len(lines),
                "normalized_records": len(records),
                "issues": len(issues),
            },
            "deterministic_order": "article_section_file_chunk_ordinal",
        }

    def normalize_record(
        self,
        record: dict,
        *,
        source_path: Path,
        source_id: str,
        line_number: int,
        default_language: str,
        source_format: str = "jsonl",
    ) -> list[dict]:
        file_hint = str(record.get("file") or record.get("path") or source_path.name).strip() or source_path.name
        article_title = str(record.get("article_title") or record.get("title") or "").strip()
        if not article_title:
            article_title = Path(file_hint).stem.replace("_", " ").replace("-", " ").strip().title() or source_id
        section_title = str(record.get("section_title") or record.get("heading") or "Overview").strip() or "Overview"
        language = (
            str(record.get("language") or record.get("lang") or default_language).strip().lower() or default_language
        )
        content = str(record.get("content") or record.get("text") or record.get("body") or "").strip()
        if not content:
            raise ValueError("missing_content")
        revision = str(record.get("revision") or record.get("revision_id") or "").strip() or None
        import_revision = str(record.get("import_revision") or revision or "").strip() or None
        chunks = split_wiki_content(content, max_chars=700)
        article_slug = wiki_article_slug(article_title)
        normalized: list[dict] = []
        for index, chunk_text in enumerate(chunks, start=1):
            digest = hashlib.sha1(
                f"{source_id}|{article_title}|{section_title}|{chunk_text}".encode("utf-8")
            ).hexdigest()[:16]
            normalized.append(
                {
                    "kind": "wiki_section_chunk",
                    "id": f"{article_slug}:{line_number}:{index}",
                    "chunk_id": f"wiki:{digest}",
                    "chunk_ordinal": index,
                    "file": file_hint,
                    "article_title": article_title,
                    "wiki_article_id": article_slug,
                    "section_title": section_title,
                    "language": language,
                    "revision": revision,
                    "import_revision": import_revision,
                    "import_metadata": {
                        "source_scope": "wiki",
                        "source_id": source_id,
                        "source_line": line_number,
                        "source_path": str(source_path),
                        "format": source_format,
                    },
                    "content": chunk_text,
                }
            )
        return normalized


def wiki_article_slug(article_title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(article_title or "").strip().lower()).strip("-")
    return normalized or "wiki-article"


__all__ = ["WikiJsonlImporter", "wiki_article_slug"]
