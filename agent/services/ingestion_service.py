from __future__ import annotations

import hashlib
import bz2
import gzip
import json
import logging
import re
import shutil
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)


class IngestionService:
    """Coordinates raw storage, metadata persistence and extraction."""

    def __init__(self, artifact_store=None, extraction_service=None) -> None:
        self._artifact_store = artifact_store or get_artifact_store()
        self._extraction_service = extraction_service or get_extraction_service()

    def upload_artifact(
        self,
        *,
        filename: str,
        content: bytes,
        created_by: str | None,
        media_type: str | None = None,
        collection_name: str | None = None,
    ) -> tuple[ArtifactDB, ArtifactVersionDB, KnowledgeCollectionDB | None]:
        artifact = artifact_repo.save(
            ArtifactDB(
                created_by=created_by,
                status="stored",
                artifact_metadata={"ingestion_mode": "raw_artifact_store"},
            )
        )
        stored = self._artifact_store.store_bytes(
            artifact_id=artifact.id,
            version_number=1,
            filename=filename,
            content=content,
            media_type=media_type,
        )
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
        artifact.latest_version_id = version.id
        artifact.latest_sha256 = version.sha256
        artifact.latest_media_type = version.media_type
        artifact.latest_filename = version.original_filename
        artifact.size_bytes = version.size_bytes
        artifact.updated_at = time.time()
        artifact = artifact_repo.save(artifact)

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

    def _split_wiki_content(self, content: str, *, max_chars: int = 700) -> list[str]:
        text = re.sub(r"\s+", " ", str(content or "").strip())
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]
        chunks: list[str] = []
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", text):
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

    def _article_slug(self, article_title: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", str(article_title or "").strip().lower()).strip("-")
        return normalized or "wiki-article"

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

    def import_wiki_xml(
        self,
        *,
        corpus_path: str,
        index_path: str | None = None,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        write_jsonl_cache: bool = True,
    ) -> dict[str, object]:
        path = Path(str(corpus_path or "").strip()).expanduser().resolve()
        if not path.exists():
            raise ValueError("wiki_corpus_not_found")
        if not path.is_file():
            raise ValueError("wiki_corpus_not_file")
        resolved_index_path = Path(str(index_path or "").strip()).expanduser().resolve() if index_path else None
        if resolved_index_path is not None and not resolved_index_path.exists():
            raise ValueError("wiki_multistream_index_not_found")
        normalized_source_id = str(source_id or "").strip() or Path(path.stem).stem
        records: list[dict] = []
        issues: list[dict] = []
        page_count = 0
        doc_count = 0
        item_ordinal = 0
        skipped_page_count = 0
        skipped_doc_count = 0

        for elem in self._iter_wiki_xml_items(path=path, index_path=resolved_index_path):
            local_name = self._tag_local_name(elem.tag)
            if local_name == "page":
                page_count += 1
                item_ordinal += 1
                title = str(elem.findtext(".//{*}title") or "").strip()
                text = str(elem.findtext(".//{*}revision/{*}text") or "").strip()
                cleaned = self._clean_wiki_markup(text)
                if not title or not cleaned:
                    issue = {"item": item_ordinal, "error": "missing_page_content"}
                    issues.append(issue)
                    skipped_page_count += 1
                    if strict:
                        raise ValueError("wiki_corpus_invalid_record")
                else:
                    try:
                        records.extend(
                            self._normalize_wiki_record(
                                {
                                    "article_title": title,
                                    "section_title": "Overview",
                                    "language": default_language,
                                    "content": cleaned,
                                    "file": path.name,
                                },
                                source_path=path,
                                source_id=normalized_source_id,
                                line_number=item_ordinal,
                                default_language=default_language,
                                source_format="xml",
                            )
                        )
                    except ValueError as exc:
                        issue = {"item": item_ordinal, "error": str(exc)}
                        issues.append(issue)
                        skipped_page_count += 1
                        if strict:
                            raise ValueError("wiki_corpus_invalid_record") from exc
            elif local_name == "doc":
                doc_count += 1
                item_ordinal += 1
                title = str(elem.findtext("./title") or "").strip()
                abstract = str(elem.findtext("./abstract") or elem.findtext("./text") or "").strip()
                cleaned = self._clean_wiki_markup(abstract)
                if not title or not cleaned:
                    issue = {"item": item_ordinal, "error": "missing_doc_content"}
                    issues.append(issue)
                    skipped_doc_count += 1
                    if strict:
                        raise ValueError("wiki_corpus_invalid_record")
                else:
                    try:
                        records.extend(
                            self._normalize_wiki_record(
                                {
                                    "article_title": title,
                                    "section_title": "Overview",
                                    "language": default_language,
                                    "content": cleaned,
                                    "file": path.name,
                                },
                                source_path=path,
                                source_id=normalized_source_id,
                                line_number=item_ordinal,
                                default_language=default_language,
                                source_format="xml",
                            )
                        )
                    except ValueError as exc:
                        issue = {"item": item_ordinal, "error": str(exc)}
                        issues.append(issue)
                        skipped_doc_count += 1
                        if strict:
                            raise ValueError("wiki_corpus_invalid_record") from exc
            elem.clear()

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
        jsonl_cache_path = None
        if write_jsonl_cache:
            jsonl_cache_path = path.with_suffix(path.suffix + ".normalized.jsonl")
            jsonl_cache_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
                encoding="utf-8",
            )
        return {
            "source_scope": "wiki",
            "source_id": normalized_source_id,
            "corpus_path": str(path),
            "index_path": str(resolved_index_path) if resolved_index_path else None,
            "jsonl_cache_path": str(jsonl_cache_path) if jsonl_cache_path else None,
            "records": records,
            "issues": issues,
            "stats": {
                "input_pages": page_count,
                "input_docs": doc_count,
                "processed_items": item_ordinal,
                "skipped_pages": skipped_page_count,
                "skipped_docs": skipped_doc_count,
                "dropped_items": skipped_page_count + skipped_doc_count,
                "normalized_records": len(records),
                "issues": len(issues),
            },
            "deterministic_order": "article_section_file_chunk_ordinal",
            "format": "xml",
            "multistream_index": {
                "enabled": resolved_index_path is not None,
                "path": str(resolved_index_path) if resolved_index_path else None,
            },
        }

    def _normalize_wiki_record(
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
        language = str(record.get("language") or record.get("lang") or default_language).strip().lower() or default_language
        content = str(record.get("content") or record.get("text") or record.get("body") or "").strip()
        if not content:
            raise ValueError("missing_content")
        revision = str(record.get("revision") or record.get("revision_id") or "").strip() or None
        import_revision = str(record.get("import_revision") or revision or "").strip() or None
        chunks = self._split_wiki_content(content, max_chars=700)
        article_slug = self._article_slug(article_title)
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

    def import_wiki_jsonl(
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
                    self._normalize_wiki_record(
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

    def import_wiki_jsonl_from_url(
        self,
        *,
        corpus_url: str,
        index_url: str | None = None,
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        max_download_bytes: int = 20 * 1024 * 1024 * 1024,
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

        download_report = self._download_with_resume(
            url=url,
            destination=local_compressed or local_extracted,
            max_download_bytes=max_download_bytes,
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
            index_report = self._download_with_resume(
                url=str(index_url).strip(),
                destination=local_index_compressed,
                max_download_bytes=512 * 1024 * 1024,
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
        report = self.import_wiki_corpus(
            corpus_path=str(local_corpus),
            index_path=str(local_index_path) if local_index_path else None,
            source_id=source_id,
            default_language=default_language,
            strict=strict,
            import_format=None,
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

    def _download_with_resume(self, *, url: str, destination: Path, max_download_bytes: int) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing_bytes = destination.stat().st_size if destination.exists() else 0
        request = urllib.request.Request(url)
        mode = "wb"
        requested_range = False
        if existing_bytes > 0:
            request.add_header("Range", f"bytes={existing_bytes}-")
            mode = "ab"
            requested_range = True
        downloaded_bytes = existing_bytes
        status = 0
        response_headers: dict[str, Any] = {}
        restarted_without_range = False
        storage_issue = None
        with urllib.request.urlopen(request, timeout=45) as response:
            status = int(getattr(response, "status", 200) or 200)
            response_headers = {
                "content_length": str(response.headers.get("Content-Length") or "").strip() or None,
                "accept_ranges": str(response.headers.get("Accept-Ranges") or "").strip() or None,
                "etag": str(response.headers.get("ETag") or "").strip() or None,
                "last_modified": str(response.headers.get("Last-Modified") or "").strip() or None,
            }
            if existing_bytes > 0 and status == 200:
                mode = "wb"
                downloaded_bytes = 0
                restarted_without_range = True
            content_length = 0
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            expected_remaining = max(0, content_length)
            free_bytes = shutil.disk_usage(destination.parent).free
            reserve_bytes = max(128 * 1024 * 1024, max_download_bytes // 200)
            if expected_remaining > 0 and free_bytes < expected_remaining + reserve_bytes:
                storage_issue = {
                    "free_bytes": int(free_bytes),
                    "required_bytes": int(expected_remaining + reserve_bytes),
                    "reserve_bytes": int(reserve_bytes),
                }
                raise ValueError("wiki_storage_insufficient")
            with destination.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_download_bytes:
                        raise ValueError("wiki_corpus_too_large")
                    output.write(chunk)
        return {
            "bytes": int(downloaded_bytes),
            "status_code": int(status),
            "requested_range": requested_range,
            "resumed": bool(requested_range and not restarted_without_range),
            "restarted_without_range": restarted_without_range,
            "headers": response_headers,
            "storage_issue": storage_issue,
        }


ingestion_service = IngestionService()


def get_ingestion_service() -> IngestionService:
    return ingestion_service
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
            )
        if detected_format == "zim":
            raise ValueError("wiki_zim_import_not_supported")
        raise ValueError("wiki_corpus_unknown_format")
