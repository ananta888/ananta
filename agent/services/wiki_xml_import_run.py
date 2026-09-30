"""Resumable, compact-filtered import of one MediaWiki XML corpus.

``IngestionService.import_wiki_xml`` coordinates the collaborators; this module
owns the per-run state (counters, resume offsets, JSONL cache handles) and the
item pipeline: parse -> normalize -> compact filter -> cache/link write ->
checkpoint.  Keeping that state in one run object lets each step stay small.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.services.wiki_import_reporter import build_wiki_import_stats

logger = logging.getLogger(__name__)

MAX_LINK_TARGETS_PER_ARTICLE = 60
MAX_REPORT_RECORDS = 1000
PROGRESS_CALLBACK_EVERY_ITEMS = 500
CHUNKS_SIDECAR_EVERY_ITEMS = 50_000


@dataclass(frozen=True)
class WikiXmlImportPaths:
    """Output files of one import, all co-located with the corpus."""

    partial_cache: Path
    partial_links: Path
    chunks_cache: Path
    final_cache: Path
    final_links: Path

    @classmethod
    def for_corpus(cls, path: Path) -> WikiXmlImportPaths:
        return cls(
            partial_cache=path.parent / (path.name + ".partial.jsonl"),
            partial_links=path.parent / (path.name + ".partial.links.jsonl"),
            chunks_cache=path.parent / (path.name + ".partial.chunks_cache.json"),
            final_cache=path.parent / (path.name + ".normalized.jsonl"),
            final_links=path.parent / (path.name + ".links.jsonl"),
        )


def resolve_wiki_xml_sources(corpus_path: str, index_path: str | None) -> tuple[Path, Path | None]:
    path = Path(str(corpus_path or "").strip()).expanduser().resolve()
    if not path.exists():
        raise ValueError("wiki_corpus_not_found")
    if not path.is_file():
        raise ValueError("wiki_corpus_not_file")
    resolved_index_path = Path(str(index_path or "").strip()).expanduser().resolve() if index_path else None
    if resolved_index_path is not None and not resolved_index_path.exists():
        raise ValueError("wiki_multistream_index_not_found")
    return path, resolved_index_path


def write_chunks_sidecar(paths: WikiXmlImportPaths, *, at_item: int, chunks: dict[str, int]) -> None:
    paths.chunks_cache.write_text(
        json.dumps({"at_item": at_item, "chunks": chunks}, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_chunks_sidecar(paths: WikiXmlImportPaths, prior_items: int) -> dict[str, int]:
    if not paths.chunks_cache.exists():
        return {}
    try:
        cache = json.loads(paths.chunks_cache.read_text(encoding="utf-8"))
        if int(cache.get("at_item") or 0) >= prior_items:
            chunks_per_article = dict(cache.get("chunks") or {})
            logger.info(
                "import_wiki_xml: loaded chunks_per_article from sidecar (%d articles, at_item=%d)",
                len(chunks_per_article),
                prior_items,
            )
            return chunks_per_article
    except Exception as exc:
        logger.warning("import_wiki_xml: chunks sidecar load failed (%s), falling back to scan", exc)
    return {}


def _scan_partial_cache_chunk_counts(paths: WikiXmlImportPaths) -> dict[str, int]:
    chunks_per_article: dict[str, int] = {}
    with paths.partial_cache.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                title = str(record.get("article_title") or "")
                chunks_per_article[title] = chunks_per_article.get(title, 0) + 1
            except json.JSONDecodeError:
                pass
    return chunks_per_article


def prepare_resume(
    paths: WikiXmlImportPaths, checkpoint: dict, *, write_jsonl_cache: bool
) -> tuple[int, dict[str, int]]:
    """Return ``(resume_from_item, chunks_per_article)`` and drop stale partial files."""
    if not (write_jsonl_cache and paths.partial_cache.exists()):
        return 0, {}
    prior_items = int(checkpoint.get("processed_items") or 0)
    if prior_items <= 0:
        paths.partial_cache.unlink(missing_ok=True)
        paths.partial_links.unlink(missing_ok=True)
        paths.chunks_cache.unlink(missing_ok=True)
        return 0, {}
    # Fast path: load chunks_per_article from sidecar if it matches checkpoint
    chunks_per_article = _load_chunks_sidecar(paths, prior_items)
    # Slow path: scan partial.jsonl only if sidecar missing/stale
    if not chunks_per_article:
        logger.info("import_wiki_xml: resuming from item %d — scanning partial cache for chunk counts", prior_items)
        chunks_per_article = _scan_partial_cache_chunk_counts(paths)
        logger.info("import_wiki_xml: resume scan done — %d articles, saving sidecar", len(chunks_per_article))
        try:
            write_chunks_sidecar(paths, at_item=prior_items, chunks=chunks_per_article)
        except Exception as exc:
            logger.warning("import_wiki_xml: could not write chunks sidecar: %s", exc)
    return prior_items, chunks_per_article


def compact_link_targets(title: str, raw_links) -> list[str]:
    """Inter-article link targets of one article, self links dropped, capped."""
    targets: list[str] = []
    for link in raw_links or []:
        link = str(link or "").strip()
        if link and link != title:
            targets.append(link)
            if len(targets) >= MAX_LINK_TARGETS_PER_ARTICLE:
                break
    return targets


@dataclass
class WikiXmlImportRun:
    """State and item pipeline of one resumable MediaWiki XML import."""

    path: Path
    index_path: Path | None
    source_id: str
    paths: WikiXmlImportPaths
    checkpoint_service: Any
    normalizer: Any
    record_strip_fields: frozenset[str]
    default_language: str
    strict: bool
    write_jsonl_cache: bool
    max_chunks_per_article: int
    min_content_chars: int
    progress_callback: Callable[[int, int], Any] | None = None
    cancel_check: Callable[[], bool] | None = None
    resume_from_item: int = 0
    start_block: int = 0
    use_blocks: bool = False
    page_count: int = 0
    doc_count: int = 0
    item_ordinal: int = 0
    record_count: int = 0
    link_count: int = 0
    prev_block_index: int = -1
    issues: list[dict] = field(default_factory=list)
    chunks_per_article: dict[str, int] = field(default_factory=dict)
    in_memory_records: list[dict] = field(default_factory=list)
    cache_fh: Any = None
    links_fh: Any = None

    @property
    def index_ref(self) -> str | None:
        return str(self.index_path) if self.index_path else None

    def restore_from_checkpoint(self, checkpoint: dict, *, resume_from_item: int, chunks: dict[str, int]) -> None:
        self.resume_from_item = resume_from_item
        self.chunks_per_article = chunks
        self.use_blocks = self.index_path is not None
        if not resume_from_item:
            return
        self.page_count = int(checkpoint.get("page_count") or 0)
        self.doc_count = int(checkpoint.get("doc_count") or 0)
        self.record_count = int(checkpoint.get("normalized_records") or 0)
        self.link_count = int(checkpoint.get("link_count") or 0)
        # resume_block_index+1 = first unprocessed block (blocks 0..resume_block_index are done)
        resume_block_index = int(checkpoint.get("block_index") or 0)
        # When using block seek, start FROM the next block (resume_block_index was fully written)
        if self.use_blocks and resume_block_index > 0:
            self.start_block = resume_block_index + 1

    def open_cache_files(self) -> None:
        if not self.write_jsonl_cache:
            return
        self.paths.partial_cache.parent.mkdir(parents=True, exist_ok=True)
        open_mode = "a" if self.resume_from_item > 0 else "w"
        self.cache_fh = self.paths.partial_cache.open(open_mode, encoding="utf-8")
        self.links_fh = self.paths.partial_links.open(open_mode, encoding="utf-8")

    def item_stream(self, parser):
        # Block-aware multistream: fast-seek to start_block, no item-level skip needed after
        if self.use_blocks:
            return parser.iter_pages_with_block(
                corpus_path=self.path, index_path=self.index_path, resume_block_index=self.start_block
            )
        return ((0, item) for item in parser.iter_items(corpus_path=self.path))

    def consume(self, item_stream) -> None:
        try:
            for block_index, item in item_stream:
                self._consume_item(block_index, item)
        except Exception:
            self._close_cache_files()
            raise

    def finish(self) -> dict[str, object]:
        if self.cache_fh:
            self.cache_fh.close()
            self.links_fh.close()
            self.paths.partial_cache.rename(self.paths.final_cache)
            self.paths.partial_links.rename(self.paths.final_links)
            self.paths.chunks_cache.unlink(missing_ok=True)
        if not self.record_count:
            raise ValueError("wiki_corpus_no_valid_records")
        stats = build_wiki_import_stats(
            input_pages=self.page_count,
            input_docs=self.doc_count,
            processed_items=self.item_ordinal,
            issues=self.issues,
            normalized_records=self.record_count,
        )
        self._save_checkpoint(
            {
                "phase": "completed",
                "processed_items": self.item_ordinal,
                "normalized_records": self.record_count,
                "link_count": self.link_count,
                "page_count": self.page_count,
                "doc_count": self.doc_count,
                "issues": len(self.issues),
                "index_path": self.index_ref,
            }
        )
        return self._result(stats)

    def _consume_item(self, block_index: int, item: dict) -> None:
        # Checkpoint at block boundary (prev block is now fully written)
        if block_index != self.prev_block_index and self.prev_block_index >= 0:
            self._on_block_boundary()
        self.prev_block_index = block_index
        self.item_ordinal += 1
        # Apply item-level skip when we could not fast-seek (no block_index in old checkpoint)
        if self.start_block == 0 and self.item_ordinal <= self.resume_from_item:
            return
        self._count_input_item(item)
        # With block-level seek, items within the resume block may be partially processed;
        # the item skip ensures we don't double-count items within the first resumed block.
        if self.resume_from_item and not self.use_blocks and self.item_ordinal <= self.resume_from_item:
            return
        normalized_batch, issue = self.normalizer.normalize_item(
            item=item,
            source_path=self.path,
            source_id=self.source_id,
            ordinal=self.item_ordinal,
            default_language=self.default_language,
            source_format="xml",
        )
        if issue:
            self.issues.append(issue)
            if self.strict:
                raise ValueError("wiki_corpus_invalid_record")
        for record in normalized_batch or []:
            self._accept_record(record)
        # Progress callback (non-blocking, every 500 items)
        if self.item_ordinal % PROGRESS_CALLBACK_EVERY_ITEMS == 0 and self.progress_callback:
            self.progress_callback(self.item_ordinal, self.record_count)

    def _count_input_item(self, item: dict) -> None:
        item_kind = str(item.get("kind") or "").strip().lower()
        if item_kind == "page":
            self.page_count += 1
        elif item_kind == "doc":
            self.doc_count += 1

    def _on_block_boundary(self) -> None:
        if self.cache_fh:
            self.cache_fh.flush()
        if self.links_fh:
            self.links_fh.flush()
        if self.cancel_check and self.cancel_check():
            raise ValueError("wiki_download_cancelled")
        self._save_checkpoint(
            {
                "phase": "normalizing",
                "processed_items": self.item_ordinal,
                "block_index": self.prev_block_index if self.prev_block_index >= 0 else 0,
                "normalized_records": self.record_count,
                "link_count": self.link_count,
                "page_count": self.page_count,
                "doc_count": self.doc_count,
                "issues": len(self.issues),
            }
        )
        if (
            self.write_jsonl_cache
            and self.item_ordinal > self.resume_from_item
            and self.item_ordinal % CHUNKS_SIDECAR_EVERY_ITEMS == 0
        ):
            try:
                write_chunks_sidecar(self.paths, at_item=self.item_ordinal, chunks=self.chunks_per_article)
            except Exception as exc:
                logger.warning("import_wiki_xml: chunks sidecar write failed: %s", exc)

    def _accept_record(self, record: dict) -> None:
        title = str(record.get("article_title") or "")
        content = str(record.get("content") or "")
        # Inline compact filter: skip short content and over-quota chunks
        if len(content) < self.min_content_chars:
            return
        if self.chunks_per_article.get(title, 0) >= self.max_chunks_per_article:
            return
        self.chunks_per_article[title] = self.chunks_per_article.get(title, 0) + 1
        # Write inter-article links compact (one line per article, max 60 targets)
        if self.links_fh and self.chunks_per_article[title] == 1:
            targets = compact_link_targets(title, record.get("links"))
            if targets:
                self.links_fh.write(json.dumps({"from": title, "to": targets}, ensure_ascii=False) + "\n")
                self.link_count += len(targets)
        # Strip bulky fields from stored record
        slim = {key: value for key, value in record.items() if key not in self.record_strip_fields}
        if self.cache_fh:
            self.cache_fh.write(json.dumps(slim, ensure_ascii=False) + "\n")
            self.record_count += 1
            if len(self.in_memory_records) < MAX_REPORT_RECORDS:
                self.in_memory_records.append(dict(record))
        else:
            self.in_memory_records.append(slim)
            self.record_count = len(self.in_memory_records)

    def _close_cache_files(self) -> None:
        if self.cache_fh:
            self.cache_fh.close()
        if self.links_fh:
            self.links_fh.close()

    def _save_checkpoint(self, checkpoint: dict) -> None:
        self.checkpoint_service.save(
            source_id=self.source_id,
            corpus_path=str(self.path),
            index_path=self.index_ref,
            checkpoint=checkpoint,
        )

    def _result(self, stats) -> dict[str, object]:
        return {
            "source_scope": "wiki",
            "source_id": self.source_id,
            "corpus_path": str(self.path),
            "index_path": self.index_ref,
            "jsonl_cache_path": str(self.paths.final_cache) if self.write_jsonl_cache else None,
            "links_cache_path": str(self.paths.final_links) if self.write_jsonl_cache else None,
            "records": self.in_memory_records,
            "issues": self.issues,
            "stats": stats,
            "deterministic_order": "parse_order_compact_filtered",
            "format": "xml",
            "multistream_index": {
                "enabled": self.index_path is not None,
                "path": self.index_ref,
            },
        }


__all__ = [
    "WikiXmlImportPaths",
    "WikiXmlImportRun",
    "compact_link_targets",
    "prepare_resume",
    "resolve_wiki_xml_sources",
    "write_chunks_sidecar",
]
