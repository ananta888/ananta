from __future__ import annotations

import hashlib
import gzip
import json
import logging
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

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

    def _normalize_wiki_record(
        self,
        record: dict,
        *,
        source_path: Path,
        source_id: str,
        line_number: int,
        default_language: str,
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
                        "format": "jsonl",
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
        source_id: str | None = None,
        default_language: str = "en",
        strict: bool = False,
        max_download_bytes: int = 128 * 1024 * 1024,
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
        if safe_name.endswith(".gz"):
            local_compressed = wiki_corpus_dir / safe_name
            local_jsonl = wiki_corpus_dir / f"{Path(safe_name).stem}.jsonl"
        else:
            local_compressed = None
            local_jsonl = wiki_corpus_dir / safe_name

        downloaded_bytes = 0
        with urllib.request.urlopen(url, timeout=45) as response:
            with (local_compressed or local_jsonl).open("wb") as output:
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_download_bytes:
                        raise ValueError("wiki_corpus_too_large")
                    output.write(chunk)

        if local_compressed is not None:
            with gzip.open(local_compressed, "rb") as source:
                with local_jsonl.open("wb") as output:
                    shutil.copyfileobj(source, output)

        report = self.import_wiki_jsonl(
            corpus_path=str(local_jsonl),
            source_id=source_id,
            default_language=default_language,
            strict=strict,
        )
        report["download"] = {
            "url": url,
            "bytes": downloaded_bytes,
            "stored_path": str(local_jsonl),
            "compressed_path": str(local_compressed) if local_compressed else None,
        }
        return report


ingestion_service = IngestionService()


def get_ingestion_service() -> IngestionService:
    return ingestion_service
