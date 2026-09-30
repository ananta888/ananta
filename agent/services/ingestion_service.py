from __future__ import annotations

import bz2
import gzip
import json
import logging
import re
import shutil
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from agent.config import settings
from agent.db_models import ArtifactDB, ArtifactVersionDB, ExtractedDocumentDB, KnowledgeCollectionDB, KnowledgeLinkDB
from agent.repository import (
    artifact_repo,
    artifact_version_repo,
    extracted_document_repo,
    knowledge_collection_repo,
    knowledge_link_repo,
)
from agent.services.artifact_store import get_artifact_store
from agent.services.extraction_service import get_extraction_service
from agent.services.wiki_corpus_downloader import ResumableCorpusDownloader
from agent.services.wiki_import_checkpoint_service import WikiImportCheckpointService
from agent.services.wiki_jsonl_importer import WikiJsonlImporter
from agent.services.wiki_mediawiki_xml_parser import MediaWikiXmlDumpParser
from agent.services.wiki_normalizer import WikiRecordNormalizer
from agent.services.wiki_record_writer import sort_wiki_records, write_wiki_jsonl_cache
from agent.services.wiki_xml_import_run import WikiXmlImportPaths, WikiXmlImportRun, prepare_resume, resolve_wiki_xml_sources

logger = logging.getLogger(__name__)


class IngestionService:
    """Coordinates raw storage, metadata persistence and extraction."""

    def __init__(
        self,
        artifact_store=None,
        extraction_service=None,
        *,
        corpus_downloader: ResumableCorpusDownloader | None = None,
        wiki_jsonl_importer: WikiJsonlImporter | None = None,
    ) -> None:
        self._artifact_store = artifact_store or get_artifact_store()
        self._extraction_service = extraction_service or get_extraction_service()
        self._corpus_downloader = corpus_downloader or ResumableCorpusDownloader()
        self._wiki_jsonl_importer = wiki_jsonl_importer or WikiJsonlImporter()
        self._wiki_parser = MediaWikiXmlDumpParser()
        self._wiki_normalizer = WikiRecordNormalizer()
        self._wiki_checkpoint_service = WikiImportCheckpointService()

    def upload_artifact(
        self,
        *,
        filename: str,
        content: bytes,
        created_by: str | None,
        media_type: str | None = None,
        collection_name: str | None = None,
        execution_checkpoint: Callable[[], None] | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[ArtifactDB, ArtifactVersionDB, KnowledgeCollectionDB | None]:
        if artifact_metadata is not None and not isinstance(
            artifact_metadata,
            Mapping,
        ):
            raise ValueError("artifact_metadata_invalid")
        checkpoint = execution_checkpoint or (lambda: None)
        checkpoint()
        artifact = artifact_repo.save(
            ArtifactDB(
                created_by=created_by,
                status="stored",
                artifact_metadata={
                    "ingestion_mode": "raw_artifact_store",
                    **dict(artifact_metadata or {}),
                },
            )
        )
        checkpoint()
        store_kwargs: dict[str, Any] = {
            "artifact_id": artifact.id,
            "version_number": 1,
            "filename": filename,
            "content": content,
            "media_type": media_type,
        }
        if execution_checkpoint is not None:
            store_kwargs["execution_checkpoint"] = execution_checkpoint
        stored = self._artifact_store.store_bytes(**store_kwargs)
        checkpoint()
        version = artifact_version_repo.save(
            ArtifactVersionDB(
                artifact_id=artifact.id,
                version_number=1,
                storage_path=stored["storage_path"],
                original_filename=stored["filename"],
                media_type=stored["media_type"],
                size_bytes=stored["size_bytes"],
                sha256=stored["sha256"],
                version_metadata={"versioning_ready": True},
            )
        )
        checkpoint()
        artifact.latest_version_id = version.id
        artifact.latest_sha256 = version.sha256
        artifact.latest_media_type = version.media_type
        artifact.latest_filename = version.original_filename
        artifact.size_bytes = version.size_bytes
        artifact.updated_at = time.time()
        artifact = artifact_repo.save(artifact)
        checkpoint()

        collection = None
        if collection_name:
            collection = knowledge_collection_repo.get_by_name(collection_name)
            if collection is None:
                collection = knowledge_collection_repo.save(
                    KnowledgeCollectionDB(name=collection_name, created_by=created_by)
                )
            knowledge_link_repo.save(
                KnowledgeLinkDB(
                    collection_id=collection.id,
                    artifact_id=artifact.id,
                    link_type="artifact",
                    link_metadata={"source": "artifact_upload", "collection_name": collection.name},
                )
            )

        checkpoint()
        return artifact, version, collection

    def extract_artifact(self, artifact_id: str) -> tuple[ArtifactDB | None, ArtifactVersionDB | None, ExtractedDocumentDB | None]:
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is None or not artifact.latest_version_id:
            return artifact, None, None

        version = artifact_version_repo.get_by_id(artifact.latest_version_id)
        if version is None:
            return artifact, None, None

        extracted = self._extraction_service.extract(
            storage_path=version.storage_path,
            filename=version.original_filename,
            media_type=version.media_type,
        )
        document = extracted_document_repo.save(
            ExtractedDocumentDB(
                artifact_id=artifact.id,
                artifact_version_id=version.id,
                extraction_status=extracted["extraction_status"],
                extraction_mode=extracted["extraction_mode"],
                text_content=extracted["text_content"],
                document_metadata=extracted["metadata"],
            )
        )
        artifact.status = extracted["extraction_mode"]
        artifact.updated_at = time.time()
        artifact_repo.save(artifact)
        return artifact, version, document

    def _tag_local_name(self, tag: str) -> str:
        return str(tag or "").rsplit("}", 1)[-1].strip().lower()

    def _clean_wiki_markup(self, raw_text: str) -> str:
        text = str(raw_text or "")
        if not text:
            return ""
        # Basic wikitext cleanup for retrieval quality without full MediaWiki parsing.
        text = re.sub(r"\{\{[^{}]{0,4000}\}\}", " ", text)
        text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
        text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
        text = re.sub(r"==+\s*([^=\n]+?)\s*==+", r" \1 ", text)
        text = re.sub(r"<ref[^>/]*>.*?</ref>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<ref[^>]*/>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _open_text_or_bz2_lines(self, path: Path):
        if path.name.endswith(".bz2"):
            return bz2.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.endswith(".gz"):
            return gzip.open(path, "rt", encoding="utf-8", errors="replace")
        return path.open("rt", encoding="utf-8", errors="replace")

    def _read_multistream_offsets(self, index_path: Path) -> list[int]:
        offsets: set[int] = set()
        with self._open_text_or_bz2_lines(index_path) as lines:
            for line in lines:
                value = str(line or "").split(":", 1)[0].strip()
                if not value:
                    continue
                try:
                    offsets.add(int(value))
                except ValueError:
                    continue
        return sorted(offsets)

    def _iter_multistream_pages(self, *, corpus_path: Path, index_path: Path):
        offsets = self._read_multistream_offsets(index_path)
        if not offsets:
            raise ValueError("wiki_multistream_index_empty")
        file_size = corpus_path.stat().st_size
        offsets = [offset for offset in offsets if 0 <= offset < file_size]
        if not offsets:
            raise ValueError("wiki_multistream_index_no_valid_offsets")
        with corpus_path.open("rb") as source:
            for position, offset in enumerate(offsets):
                next_offset = offsets[position + 1] if position + 1 < len(offsets) else file_size
                if next_offset <= offset:
                    continue
                source.seek(offset)
                compressed_block = source.read(next_offset - offset)
                if not compressed_block:
                    continue
                try:
                    xml_fragment = bz2.decompress(compressed_block)
                except OSError as exc:
                    logger.warning("Wiki multistream block could not be decompressed", extra={"offset": offset, "error": str(exc)})
                    continue
                wrapped = b"<mediawiki>" + xml_fragment + b"</mediawiki>"
                context = ET.iterparse(BytesIO(wrapped), events=("end",))
                for _event, elem in context:
                    if self._tag_local_name(elem.tag) == "page":
                        yield elem
                        elem.clear()

    def _iter_wiki_xml_items(self, *, path: Path, index_path: Path | None = None):
        if index_path is not None:
            yield from self._iter_multistream_pages(corpus_path=path, index_path=index_path)
            return
        if path.name.endswith(".bz2"):
            raw_stream = bz2.open(path, "rb")
        elif path.name.endswith(".gz"):
            raw_stream = gzip.open(path, "rb")
        else:
            raw_stream = path.open("rb")
        with raw_stream as stream:
            context = ET.iterparse(stream, events=("end",))
            for _event, elem in context:
                local_name = self._tag_local_name(elem.tag)
                if local_name in {"page", "doc"}:
                    yield elem
                    elem.clear()

    def _infer_wiki_format(self, *, corpus_path: Path, import_format: str | None = None) -> str:
        explicit = str(import_format or "").strip().lower()
        if explicit in {"jsonl", "mediawiki-jsonl"}:
            return "jsonl"
        if explicit in {"xml", "mediawiki-xml", "mediawiki-multistream"}:
            return "xml"
        if explicit == "zim":
            return "zim"
        name = str(corpus_path.name).lower()
        if name.endswith(".jsonl"):
            return "jsonl"
        if name.endswith(".xml") or name.endswith(".xml.gz") or name.endswith(".xml.bz2"):
            return "xml"
        if name.endswith(".zim"):
            return "zim"
        raise ValueError("wiki_corpus_unknown_format")

    def import_wiki_corpus(
        self,
        *,
        corpus_path: str,
        index_path: str | None = None,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        import_format: str | None = None,
        progress_callback=None,
        cancel_check=None,
        max_chunks_per_article: int = 3,
        min_content_chars: int = 1,
    ) -> dict[str, object]:
        path = Path(str(corpus_path or "").strip()).expanduser().resolve()
        detected_format = self._infer_wiki_format(corpus_path=path, import_format=import_format)
        if detected_format == "jsonl":
            return self.import_wiki_jsonl(
                corpus_path=str(path),
                source_id=source_id,
                default_language=default_language,
                strict=strict,
            )
        if detected_format == "xml":
            return self.import_wiki_xml(
                corpus_path=str(path),
                index_path=index_path,
                source_id=source_id,
                default_language=default_language,
                strict=strict,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                max_chunks_per_article=max_chunks_per_article,
                min_content_chars=min_content_chars,
            )
        if detected_format == "zim":
            raise ValueError("wiki_zim_import_not_supported")
        raise ValueError("wiki_corpus_unknown_format")

    # Fields stripped from records before writing to save space.
    # Links are extracted separately to the links file; categories go to nodes.
    _RECORD_STRIP_FIELDS = frozenset({"links", "categories", "import_metadata"})

    def import_wiki_xml(
        self,
        *,
        corpus_path: str,
        index_path: str | None = None,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        write_jsonl_cache: bool = True,
        progress_callback=None,
        cancel_check=None,
        max_chunks_per_article: int = 3,
        min_content_chars: int = 1,
    ) -> dict[str, object]:
        path, resolved_index_path = resolve_wiki_xml_sources(corpus_path, index_path)
        normalized_source_id = str(source_id or "").strip() or Path(path.stem).stem
        paths = WikiXmlImportPaths.for_corpus(path)
        # Load checkpoint for resume
        checkpoint = (
            self._wiki_checkpoint_service.load(
                source_id=normalized_source_id,
                corpus_path=str(path),
                index_path=str(resolved_index_path) if resolved_index_path else None,
            )
            or {}
        )
        resume_from_item, chunks_per_article = prepare_resume(
            paths, checkpoint, write_jsonl_cache=write_jsonl_cache
        )
        run = WikiXmlImportRun(
            path=path,
            index_path=resolved_index_path,
            source_id=normalized_source_id,
            paths=paths,
            checkpoint_service=self._wiki_checkpoint_service,
            normalizer=self._wiki_normalizer,
            record_strip_fields=self._RECORD_STRIP_FIELDS,
            default_language=default_language,
            strict=strict,
            write_jsonl_cache=write_jsonl_cache,
            max_chunks_per_article=max_chunks_per_article,
            min_content_chars=min_content_chars,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        run.restore_from_checkpoint(checkpoint, resume_from_item=resume_from_item, chunks=chunks_per_article)
        run.open_cache_files()
        run.consume(run.item_stream(self._wiki_parser))
        return run.finish()

    def _load_jsonl_for_indexing(self, path: Path) -> list[dict]:
        """Stream-reads a JSONL file line by line to avoid one big string allocation."""
        records = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def import_wiki_jsonl(
        self,
        *,
        corpus_path: str,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
    ) -> dict[str, object]:
        return self._wiki_jsonl_importer.import_file(
            corpus_path=corpus_path,
            source_id=source_id,
            default_language=default_language,
            strict=strict,
        )

    def import_wiki_jsonl_from_url(
        self,
        *,
        corpus_url: str,
        index_url: str | None = None,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        max_download_bytes: int = 20 * 1024 * 1024 * 1024,
        cancel_check=None,
        progress_callback=None,
        max_chunks_per_article: int = 3,
        min_content_chars: int = 1,
    ) -> dict[str, object]:
        url = str(corpus_url or "").strip()
        if not url:
            raise ValueError("wiki_corpus_url_required")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("wiki_corpus_url_invalid_scheme")

        wiki_corpus_dir = Path(settings.data_dir) / "wiki_corpora"
        wiki_corpus_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(parsed.path or "").name or "wiki-corpus.jsonl"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-") or "wiki-corpus.jsonl"
        if safe_name.endswith(".gz") or safe_name.endswith(".bz2"):
            local_compressed = wiki_corpus_dir / safe_name
            local_extracted = wiki_corpus_dir / Path(safe_name).stem
        else:
            local_compressed = None
            local_extracted = wiki_corpus_dir / safe_name

        # Fast-path: if normalized.jsonl already exists, skip download and extraction entirely.
        # Infer corpus path from the compressed/extracted path without actually downloading.
        _pre_corpus = local_compressed or local_extracted
        _pre_jsonl  = _pre_corpus.parent / (_pre_corpus.name + ".normalized.jsonl")
        _pre_links  = _pre_corpus.parent / (_pre_corpus.name + ".links.jsonl")
        if _pre_jsonl.exists() and _pre_jsonl.stat().st_size > 0:
            logger.info(
                "import_wiki_jsonl_from_url: pre-check JSONL cache hit %s — skipping download/extraction",
                _pre_jsonl.name,
            )
            normalized_source_id = str(source_id or "").strip() or _pre_corpus.stem
            checkpoint = self._wiki_checkpoint_service.load(
                source_id=normalized_source_id,
                corpus_path=str(_pre_corpus),
                index_path=None,
            ) or {}
            return {
                "source_scope": "wiki",
                "source_id": normalized_source_id,
                "corpus_path": str(_pre_corpus),
                "jsonl_cache_path": str(_pre_jsonl),
                "links_cache_path": str(_pre_links) if _pre_links.exists() else None,
                "records": [],
                "issues": [],
                "stats": {
                    "page_count":   checkpoint.get("page_count", 0),
                    "doc_count":    checkpoint.get("doc_count", 0),
                    "record_count": checkpoint.get("normalized_records", 0),
                    "link_count":   checkpoint.get("link_count", 0),
                },
                "deterministic_order": "parse_order_compact_filtered",
                "format": "jsonl_cache",
                "cache_hit": True,
                "download": {"url": url, "skipped": True},
            }

        download_report = self._corpus_downloader.download(
            url=url,
            destination=local_compressed or local_extracted,
            max_download_bytes=max_download_bytes,
            cancel_check=cancel_check,
        )

        lower_safe_name = safe_name.lower()
        if local_compressed is not None and lower_safe_name.endswith(".jsonl.gz"):
            if str(local_compressed).endswith(".gz"):
                source_stream = gzip.open(local_compressed, "rb")
            else:
                source_stream = bz2.open(local_compressed, "rb")
            with source_stream as source:
                with local_extracted.open("wb") as output:
                    shutil.copyfileobj(source, output)
            local_corpus = local_extracted
        else:
            local_corpus = local_compressed or local_extracted

        lower_name = str(local_corpus.name).lower()
        local_index_path = None
        index_download = None
        if index_url:
            parsed_index = urllib.parse.urlparse(str(index_url).strip())
            if parsed_index.scheme not in {"https", "http"}:
                raise ValueError("wiki_index_url_invalid_scheme")
            index_name = Path(parsed_index.path or "").name or "wiki-index.txt.bz2"
            safe_index_name = re.sub(r"[^A-Za-z0-9._-]+", "-", index_name).strip("-") or "wiki-index.txt.bz2"
            local_index_compressed = wiki_corpus_dir / safe_index_name
            local_index_path = wiki_corpus_dir / Path(safe_index_name).stem if safe_index_name.endswith(".bz2") else local_index_compressed
            index_report = self._corpus_downloader.download(
                url=str(index_url).strip(),
                destination=local_index_compressed,
                max_download_bytes=512 * 1024 * 1024,
                cancel_check=cancel_check,
            )
            if safe_index_name.endswith(".bz2"):
                with bz2.open(local_index_compressed, "rb") as source:
                    with local_index_path.open("wb") as output:
                        shutil.copyfileobj(source, output)
            index_download = {
                "url": str(index_url).strip(),
                **index_report,
                "stored_path": str(local_index_path),
                "compressed_path": str(local_index_compressed) if safe_index_name.endswith(".bz2") else None,
            }
        # If a completed normalized JSONL cache exists alongside the corpus, return paths directly
        # without loading 7.5 GB into RAM — the indexing layer reads it streaming.
        jsonl_cache_candidate = local_corpus.parent / (local_corpus.name + ".normalized.jsonl")
        links_cache_candidate = local_corpus.parent / (local_corpus.name + ".links.jsonl")
        if jsonl_cache_candidate.exists() and jsonl_cache_candidate.stat().st_size > 0:
            logger.info(
                "import_wiki_jsonl_from_url: JSONL cache hit %s (%d bytes) — returning paths, skipping XML parse",
                jsonl_cache_candidate.name,
                jsonl_cache_candidate.stat().st_size,
            )
            normalized_source_id = str(source_id or "").strip() or local_corpus.stem
            checkpoint = self._wiki_checkpoint_service.load(
                source_id=normalized_source_id,
                corpus_path=str(local_corpus),
                index_path=str(local_index_path) if local_index_path else None,
            ) or {}
            report = {
                "source_scope": "wiki",
                "source_id": normalized_source_id,
                "corpus_path": str(local_corpus),
                "jsonl_cache_path": str(jsonl_cache_candidate),
                "links_cache_path": str(links_cache_candidate) if links_cache_candidate.exists() else None,
                "records": [],
                "issues": [],
                "stats": {
                    "page_count":    checkpoint.get("page_count", 0),
                    "doc_count":     checkpoint.get("doc_count", 0),
                    "record_count":  checkpoint.get("normalized_records", 0),
                    "link_count":    checkpoint.get("link_count", 0),
                },
                "deterministic_order": "parse_order_compact_filtered",
                "format": "jsonl_cache",
                "cache_hit": True,
            }
        else:
            report = self.import_wiki_corpus(
                corpus_path=str(local_corpus),
                index_path=str(local_index_path) if local_index_path else None,
                source_id=source_id,
                default_language=default_language,
                strict=strict,
                import_format=None,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                max_chunks_per_article=max_chunks_per_article,
                min_content_chars=min_content_chars,
            )
        report["download"] = {
            "url": url,
            **download_report,
            "stored_path": str(local_corpus),
            "compressed_path": str(local_compressed) if local_compressed else None,
            "resumable": True,
            "index": index_download,
        }
        return report


ingestion_service = IngestionService()


def get_ingestion_service() -> IngestionService:
    return ingestion_service
