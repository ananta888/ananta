from __future__ import annotations

import hashlib
from typing import Any

from agent.services.wiki_chunking_policy import split_wiki_content
from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy
from agent.sources.open_notebook_mapper import SOURCE_SYSTEM, slugify

NOTE_SOURCE_SYSTEM = "open_notebook_note"
_VALID_NOTE_TYPES = {"human", "ai", "unknown"}


class OpenNotebookNotesImporter:
    """Imports OpenNotebook notes as controlled artifacts.

    Notes are human/AI annotations, not source truth: they get
    record_kind='note', a configurable-but-lower retrieval priority and are
    never promoted to primary sources. Chat session content never enters
    through this path.
    """

    def __init__(self, *, ingestion_service, artifact_repository, policy: OpenNotebookImportPolicy | None = None) -> None:
        self._ingestion_service = ingestion_service
        self._artifact_repo = artifact_repository
        self._policy = policy or OpenNotebookImportPolicy()

    def import_notes(
        self,
        notes: list[dict[str, Any]],
        *,
        import_key: str,
        registry_source_id: str,
        collection_names_by_notebook: dict[str, str],
        created_by: str | None = None,
    ) -> dict[str, Any]:
        imported = 0
        skipped = 0
        failed = 0
        issues: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        artifact_ids: list[str] = []

        for note in notes:
            note_id = str(note.get("id") or "").strip()
            decision = self._policy.evaluate_record(note, section="notes")
            if not decision.allowed:
                skipped += 1
                issues.append({"reason_code": decision.reason_code, "note_id": note_id})
                continue
            note_type = str(note.get("note_type") or "unknown").strip().lower()
            if note_type not in _VALID_NOTE_TYPES:
                note_type = "unknown"
            content = str(note.get("content") or "").strip()
            if not content:
                skipped += 1
                issues.append({"reason_code": "note_empty_content", "note_id": note_id})
                continue
            title = str(note.get("title") or f"Note {note_id}")
            notebook_id = str(note.get("notebook_id") or "")
            collection_name = collection_names_by_notebook.get(notebook_id) or None
            try:
                artifact, _version, _collection = self._ingestion_service.upload_artifact(
                    filename=f"note-{slugify(note_id, fallback=slugify(title))}.md",
                    content=f"# {title}\n\n{content}\n".encode("utf-8"),
                    created_by=created_by,
                    media_type="text/markdown",
                    collection_name=collection_name,
                )
                metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
                metadata["ingestion_mode"] = "open_notebook_import"
                metadata["source_system"] = NOTE_SOURCE_SYSTEM
                metadata["record_kind"] = "note"
                metadata["note_type"] = note_type
                metadata["open_notebook"] = {
                    "note_id": note_id,
                    "notebook_id": notebook_id,
                    "source_id": str(note.get("source_id") or "") or None,
                    "import_key": import_key,
                    "registry_source_id": registry_source_id,
                }
                metadata["sanitized"] = dict(decision.sanitized_metadata or {})
                artifact.artifact_metadata = metadata
                artifact = self._artifact_repo.save(artifact)
            except Exception as exc:  # noqa: BLE001 - collected as import issue
                failed += 1
                issues.append({"reason_code": "note_import_failed", "note_id": note_id, "detail": str(exc)})
                continue
            imported += 1
            artifact_ids.append(str(artifact.id))
            records.extend(
                self._build_note_records(
                    note=note,
                    note_type=note_type,
                    title=title,
                    content=content,
                    artifact_id=str(artifact.id),
                    registry_source_id=registry_source_id,
                    import_key=import_key,
                    collection_name=collection_name,
                )
            )

        return {
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "issues": issues,
            "records": records,
            "artifact_ids": artifact_ids,
        }

    def _build_note_records(
        self,
        *,
        note: dict[str, Any],
        note_type: str,
        title: str,
        content: str,
        artifact_id: str,
        registry_source_id: str,
        import_key: str,
        collection_name: str | None,
    ) -> list[dict[str, Any]]:
        note_id = str(note.get("id") or "")
        records: list[dict[str, Any]] = []
        for ordinal, chunk_text in enumerate(split_wiki_content(content, max_chars=700), start=1):
            digest = hashlib.sha256(f"note|{note_id}|{chunk_text}".encode("utf-8")).hexdigest()[:16]
            records.append(
                {
                    "kind": "open_notebook_note_chunk",
                    "id": f"note-{slugify(note_id)}:{ordinal}",
                    "chunk_id": f"onb-note:{digest}",
                    "file": f"open-notebook/notes/{slugify(note_id)}.md",
                    "title": title,
                    "content": chunk_text,
                    "import_metadata": {
                        "source_scope": "open_notebook",
                        "source_system": NOTE_SOURCE_SYSTEM,
                        "source_type": "open_notebook",
                        "registry_source_id": registry_source_id,
                        "open_notebook_note_id": note_id,
                        "open_notebook_source_id": str(note.get("source_id") or "") or None,
                        "artifact_id": artifact_id,
                        "record_kind": "note",
                        "note_type": note_type,
                        "retrieval_priority": "low",
                        "collection_names": [collection_name] if collection_name else [],
                        "import_key": import_key,
                        "source_title": title,
                    },
                }
            )
        return records
