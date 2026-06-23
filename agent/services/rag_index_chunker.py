from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from agent.services.wiki_codecompass_bridge import WikiCodeCompassBridge


def wiki_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or "wiki-article"


def split_wiki_text(text: str, *, max_chars: int = 700) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        candidate = sentence.strip()
        if not candidate:
            continue
        if not current:
            current = candidate
            continue
        if len(current) + 1 + len(candidate) <= max_chars:
            current = f"{current} {candidate}"
            continue
        chunks.append(current)
        current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_wiki_records(
    *,
    source_id: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    chunked: list[dict[str, Any]] = []
    for record in records:
        article_title = str(record.get("article_title") or record.get("title") or "").strip()
        if not article_title:
            article_title = Path(str(record.get("file") or "wiki")).stem.replace("_", " ").replace("-", " ").title()
        section_title = str(record.get("section_title") or record.get("heading") or "Overview").strip() or "Overview"
        language = str(record.get("language") or record.get("lang") or "en").strip().lower() or "en"
        content = str(record.get("content") or record.get("text") or "").strip()
        if not content:
            continue
        revision = str(record.get("revision") or record.get("revision_id") or "").strip() or None
        import_revision = str(record.get("import_revision") or revision or "").strip() or None
        file_hint = str(record.get("file") or record.get("path") or f"wiki/{wiki_slug(article_title)}.md").strip()
        article_id = str(record.get("wiki_article_id") or wiki_slug(article_title)).strip() or wiki_slug(article_title)
        for ordinal, chunk_text in enumerate(split_wiki_text(content, max_chars=700), start=1):
            digest = hashlib.sha1(
                f"{source_id}|{article_title}|{section_title}|{chunk_text}".encode("utf-8")
            ).hexdigest()[:16]
            chunked.append(
                {
                    "kind": "wiki_section_chunk",
                    "id": f"{article_id}:{ordinal}:{digest[:8]}",
                    "chunk_id": f"wiki:{digest}",
                    "chunk_ordinal": ordinal,
                    "file": file_hint,
                    "path": file_hint,
                    "article_title": article_title,
                    "wiki_article_id": article_id,
                    "section_title": section_title,
                    "language": language,
                    "revision": revision,
                    "import_revision": import_revision,
                    "import_metadata": dict(record.get("import_metadata") or {}),
                    "content": chunk_text,
                }
            )
    return sorted(
        chunked,
        key=lambda item: (
            str(item.get("article_title") or "").lower(),
            str(item.get("section_title") or "").lower(),
            str(item.get("file") or "").lower(),
            int(item.get("chunk_ordinal") or 0),
            str(item.get("chunk_id") or ""),
        ),
    )


def materialize_wiki_markdown_corpus(records: list[dict[str, Any]], *, root: Path) -> set[str]:
    corpus_root = root / "wiki"
    corpus_root.mkdir(parents=True, exist_ok=True)
    by_article: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        article_id = str(record.get("wiki_article_id") or wiki_slug(str(record.get("article_title") or ""))).strip()
        if not article_id:
            article_id = "wiki-article"
        by_article.setdefault(article_id, []).append(record)

    include_globs: set[str] = set()
    for article_id, article_records in by_article.items():
        article_title = str(article_records[0].get("article_title") or article_id).strip() or article_id
        article_file = corpus_root / f"{article_id}.md"
        article_records.sort(key=lambda item: int(item.get("chunk_ordinal") or 0))
        markdown_lines = [f"# {article_title}", ""]
        for entry in article_records:
            section_title = str(entry.get("section_title") or "Overview").strip() or "Overview"
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            markdown_lines.append(f"## {section_title}")
            markdown_lines.append("")
            markdown_lines.append(content)
            markdown_lines.append("")
        article_file.write_text("\n".join(markdown_lines).strip() + "\n", encoding="utf-8")
        include_globs.add(f"wiki/{article_file.name}")
    return include_globs


def index_wiki_records_with_codecompass(
    *,
    records=None,
    records_path=None,
    output_dir: Path,
    profile: dict[str, Any],
    links_path=None,
) -> dict[str, Any]:
    include_graph = str(profile.get("limits", {}).get("graph_export_mode") or "off").strip().lower() != "off"
    bridge = WikiCodeCompassBridge()
    manifest = bridge.build_outputs(records=records, records_path=records_path,
                                    output_dir=output_dir, include_graph=include_graph,
                                    links_path=links_path)
    return {
        **manifest,
        "profile_name": profile.get("name"),
        "generated_at": time.time(),
        "deterministic_order": "json_sort_keys",
        "error_count": 0,
    }
