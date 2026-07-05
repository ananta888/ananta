from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.services.wiki_chunking_policy import split_wiki_content
from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy
from agent.sources.open_notebook_mapper import SOURCE_SYSTEM, OpenNotebookMapper, normalize_text, slugify

logger = logging.getLogger(__name__)

OPEN_NOTEBOOK_INGESTION_MODE = "open_notebook_import"
OPEN_NOTEBOOK_SOURCE_TYPE = "open_notebook"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def registry_source_id_for_import_key(import_key: str) -> str:
    return f"open-notebook-{str(import_key or '')[:12]}"


def build_registry_descriptor(
    *,
    registry_source_id: str,
    import_key: str,
    notebook_ids: list[str],
    exported_at: str,
    export_version: str,
) -> dict[str, Any]:
    imported_at = _now_iso()
    return {
        "schema": "source_descriptor.v1",
        "source_id": registry_source_id,
        "source_type": OPEN_NOTEBOOK_SOURCE_TYPE,
        "display_name": f"OpenNotebook Export {str(import_key or '')[:8]}",
        "enabled": True,
        "trust_level": "user_managed_research",
        "fetch_source": {
            "url": f"file:///imports/open-notebook/{import_key}.json",
            "method": "GET",
            "refresh_interval": "manual",
            "cache_policy": "immutable_export",
            "expected_format": "open_notebook_export.v1",
        },
        "citation_source": {
            "canonical_url": f"file:///imports/open-notebook/{import_key}.json",
            "title": "OpenNotebook local export",
            "publisher": "OpenNotebook (user-managed research workspace)",
            "version_label": str(export_version or "1"),
            "retrieved_at": str(exported_at or imported_at) or imported_at,
            "license_ref": "unknown",
            "citation_text": "Imported from a local OpenNotebook export; user-managed research content.",
        },
        "license": {"name": "unknown", "ref": "unknown"},
        "snapshot_policy": {"immutable": True, "dedupe_by_hash": True},
        "retention_policy": {"keep_latest": 50},
        "extensions": {
            "source_system": SOURCE_SYSTEM,
            "import_key": import_key,
            "notebook_ids": list(notebook_ids),
            "imported_at": imported_at,
            "export_version": str(export_version or ""),
            "provenance": {
                "source_system": SOURCE_SYSTEM,
                "original_url": None,
                "original_file_path": f"/imports/open-notebook/{import_key}.json",
                "imported_at": imported_at,
                "export_version": str(export_version or ""),
                "license_ref": None,
                "license_status": "unknown",
            },
        },
    }


class OpenNotebookImporter:
    """Imports validated OpenNotebook exports into Ananta artifacts, collections,
    registry descriptors, snapshots and knowledge-index records.

    The import runs synchronously and returns a result object with status,
    reason_code and per-section counters. Repeated imports of the same export
    are idempotent: snapshots dedupe on content_hash, collections resolve by
    name and the registry descriptor updates in place.
    """

    def __init__(
        self,
        *,
        ingestion_service=None,
        artifact_repository=None,
        knowledge_collection_repository=None,
        knowledge_link_repository=None,
        knowledge_index_repository=None,
        source_registry=None,
        snapshot_store=None,
        policy: OpenNotebookImportPolicy | None = None,
        mapper: OpenNotebookMapper | None = None,
        index_root: Path | None = None,
        knowledge_index_factory=None,
        import_state_store=None,
    ) -> None:
        if ingestion_service is None:
            from agent.services.ingestion_service import get_ingestion_service

            ingestion_service = get_ingestion_service()
        if (
            artifact_repository is None
            or knowledge_collection_repository is None
            or knowledge_link_repository is None
            or knowledge_index_repository is None
        ):
            from agent.repository import (
                artifact_repo,
                knowledge_collection_repo,
                knowledge_index_repo,
                knowledge_link_repo,
            )

            artifact_repository = artifact_repository or artifact_repo
            knowledge_collection_repository = knowledge_collection_repository or knowledge_collection_repo
            knowledge_link_repository = knowledge_link_repository or knowledge_link_repo
            knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
        if source_registry is None:
            from agent.sources.source_registry import SourceRegistry

            source_registry = SourceRegistry()
        if snapshot_store is None:
            from agent.sources.source_snapshot_store import SourceSnapshotStore

            snapshot_store = SourceSnapshotStore()
        if knowledge_index_factory is None:
            from agent.db_models import KnowledgeIndexDB

            knowledge_index_factory = KnowledgeIndexDB
        if import_state_store is None:
            from agent.sources.open_notebook_import_state import OpenNotebookImportStateStore

            import_state_store = OpenNotebookImportStateStore()

        self._ingestion_service = ingestion_service
        self._artifact_repo = artifact_repository
        self._knowledge_collection_repo = knowledge_collection_repository
        self._knowledge_link_repo = knowledge_link_repository
        self._knowledge_index_repo = knowledge_index_repository
        self._source_registry = source_registry
        self._snapshot_store = snapshot_store
        self._policy = policy or OpenNotebookImportPolicy()
        self._mapper = mapper or OpenNotebookMapper()
        self._index_root = Path(index_root or (Path(settings.data_dir) / "knowledge_indices" / "open_notebook"))
        self._knowledge_index_factory = knowledge_index_factory
        self._import_state_store = import_state_store

    def import_export(
        self,
        payload: dict[str, Any],
        *,
        created_by: str | None = None,
        include_notes: bool | None = None,
        include_insights: bool | None = None,
    ) -> dict[str, Any]:
        try:
            plan = self._mapper.map_export(payload)
        except ValueError as exc:
            return {
                "status": "failed",
                "reason_code": str(exc).split(":", 1)[0],
                "human_message": str(exc),
                "imported": {},
                "skipped": {},
                "failed": {},
                "issues": [{"reason_code": str(exc).split(":", 1)[0], "detail": str(exc)}],
            }

        import_key = str(plan["import_key"])
        registry_source_id = registry_source_id_for_import_key(import_key)
        import_state = self._import_state_store.load(registry_source_id)
        notebook_ids = [str(item.get("external_id") or "") for item in plan["collections"]]

        descriptor = build_registry_descriptor(
            registry_source_id=registry_source_id,
            import_key=import_key,
            notebook_ids=notebook_ids,
            exported_at=str(plan.get("exported_at") or ""),
            export_version=str(plan.get("export_version") or ""),
        )
        descriptor = self._source_registry.update_source(
            source_id=registry_source_id, descriptor=descriptor, allow_create=True
        )
        descriptor_hash = str((descriptor.get("extensions") or {}).get("descriptor_hash") or "")

        issues: list[dict[str, Any]] = []
        imported = {"sources": 0, "notes": 0, "insights": 0, "collections": 0}
        skipped = {"sources": 0, "notes": 0, "insights": 0, "chat_sessions": len(plan["chat_sessions"])}
        failed = {"sources": 0, "notes": 0, "insights": 0}
        artifact_ids: list[str] = []
        snapshot_ids: list[str] = []
        collection_ids: list[str] = []
        snapshots_by_source: dict[str, dict[str, Any]] = {}
        artifacts_by_source: dict[str, Any] = {}
        imported_source_ids: set[str] = set()

        chat_decision = self._policy.evaluate_section("chat_sessions")
        if plan["chat_sessions"] and not chat_decision.allowed:
            issues.append({"reason_code": chat_decision.reason_code, "section": "chat_sessions"})

        for artifact_plan in plan["artifacts"]:
            external_id = str(artifact_plan["external_id"])
            decision = self._policy.evaluate_record(
                {
                    "title": artifact_plan["title"],
                    "full_text": artifact_plan["content"],
                    "metadata": artifact_plan["metadata"],
                },
                section="sources",
            )
            if not decision.allowed:
                skipped["sources"] += 1
                issues.append({"reason_code": decision.reason_code, "source_id": external_id})
                continue
            previous_source = dict((import_state.get("sources") or {}).get(external_id) or {})
            if str(previous_source.get("content_hash") or "") == str(artifact_plan["content_hash"]):
                skipped["sources"] += 1
                issues.append({"reason_code": "duplicate_content_hash", "source_id": external_id})
                previous_snapshot = self._snapshot_by_id(
                    registry_source_id=registry_source_id,
                    snapshot_id=str(previous_source.get("snapshot_id") or ""),
                )
                previous_artifact = self._artifact_repo.get_by_id(str(previous_source.get("artifact_id") or ""))
                if previous_snapshot is not None:
                    snapshots_by_source[external_id] = previous_snapshot
                if previous_artifact is not None:
                    artifacts_by_source[external_id] = previous_artifact
                continue
            try:
                artifact, snapshot = self._import_source(
                    artifact_plan,
                    sanitized_metadata=decision.sanitized_metadata,
                    registry_source_id=registry_source_id,
                    descriptor_hash=descriptor_hash,
                    import_key=import_key,
                    created_by=created_by,
                    collection_ids_out=collection_ids,
                    export_version=str(plan.get("export_version") or ""),
                )
            except Exception as exc:  # noqa: BLE001 - collected as import issue
                failed["sources"] += 1
                issues.append({"reason_code": "source_import_failed", "source_id": external_id, "detail": str(exc)})
                continue
            imported["sources"] += 1
            artifact_ids.append(str(artifact.id))
            snapshot_ids.append(str(snapshot["snapshot_id"]))
            snapshots_by_source[external_id] = snapshot
            artifacts_by_source[external_id] = artifact
            imported_source_ids.add(external_id)
            import_state.setdefault("sources", {})[external_id] = {
                "content_hash": str(artifact_plan["content_hash"]),
                "artifact_id": str(artifact.id),
                "snapshot_id": str(snapshot["snapshot_id"]),
            }

        notes_enabled = self._policy.allow_notes if include_notes is None else bool(include_notes)
        if plan["notes"] and notes_enabled and self._policy.allow_notes:
            from agent.sources.open_notebook_notes_importer import OpenNotebookNotesImporter

            notes_result = OpenNotebookNotesImporter(
                ingestion_service=self._ingestion_service,
                artifact_repository=self._artifact_repo,
                policy=self._policy,
            ).import_notes(
                plan["notes"],
                import_key=import_key,
                registry_source_id=registry_source_id,
                collection_names_by_notebook={
                    str(item.get("external_id") or ""): str(item.get("name") or "") for item in plan["collections"]
                },
                created_by=created_by,
                existing_state=dict(import_state.get("notes") or {}),
                snapshots_by_source=snapshots_by_source,
            )
            imported["notes"] = int(notes_result["imported"])
            skipped["notes"] = int(notes_result["skipped"])
            failed["notes"] = int(notes_result["failed"])
            issues.extend(notes_result["issues"])
            note_records = list(notes_result["records"])
            artifact_ids.extend(notes_result["artifact_ids"])
            import_state["notes"] = dict(notes_result["state"])
        else:
            skipped["notes"] = len(plan["notes"])
            note_records = []
            if plan["notes"] and not notes_enabled:
                issues.append({"reason_code": "notes_import_disabled", "section": "notes"})

        insights_enabled = self._policy.allow_insights if include_insights is None else bool(include_insights)
        if plan["source_insights"] and insights_enabled and self._policy.allow_insights:
            from agent.sources.open_notebook_insights_importer import OpenNotebookInsightsImporter

            insights_result = OpenNotebookInsightsImporter(
                ingestion_service=self._ingestion_service,
                artifact_repository=self._artifact_repo,
                policy=self._policy,
            ).import_insights(
                plan["source_insights"],
                import_key=import_key,
                registry_source_id=registry_source_id,
                snapshots_by_source=snapshots_by_source,
                artifacts_by_source=artifacts_by_source,
                created_by=created_by,
                existing_state=dict(import_state.get("insights") or {}),
            )
            imported["insights"] = int(insights_result["imported"])
            skipped["insights"] = int(insights_result["skipped"])
            failed["insights"] = int(insights_result["failed"])
            issues.extend(insights_result["issues"])
            insight_records = list(insights_result["records"])
            artifact_ids.extend(insights_result["artifact_ids"])
            import_state["insights"] = dict(insights_result["state"])
        else:
            skipped["insights"] = len(plan["source_insights"])
            insight_records = []
            if plan["source_insights"] and not insights_enabled:
                issues.append({"reason_code": "insights_import_disabled", "section": "source_insights"})

        source_records = [
            record
            for external_id, snapshot in snapshots_by_source.items()
            if external_id in imported_source_ids
            for record in self._build_source_records(
                artifact_plan=next(item for item in plan["artifacts"] if str(item["external_id"]) == external_id),
                snapshot=snapshot,
                artifact=artifacts_by_source[external_id],
                registry_source_id=registry_source_id,
                import_key=import_key,
            )
        ]
        all_records = [*source_records, *note_records, *insight_records]
        knowledge_index_id = None
        if all_records:
            knowledge_index_id = self._write_knowledge_index(
                records=all_records,
                import_key=import_key,
                registry_source_id=registry_source_id,
                created_by=created_by,
            )
        self._import_state_store.save(registry_source_id, import_state)
        current_descriptor = self._source_registry.get_source(registry_source_id)
        if current_descriptor is not None:
            extensions = dict(current_descriptor.get("extensions") or {})
            extensions["record_counts"] = {
                "primary_sources": len(dict(import_state.get("sources") or {})),
                "notes": len(dict(import_state.get("notes") or {})),
                "derived_insights": len(
                    [
                        key
                        for key in dict(import_state.get("insights") or {})
                        if not str(key).startswith("transformation:")
                    ]
                ),
            }
            current_descriptor["extensions"] = extensions
            self._source_registry.update_source(
                source_id=registry_source_id,
                descriptor=current_descriptor,
                allow_create=False,
            )

        imported["collections"] = len(set(collection_ids))
        status = "completed"
        if failed["sources"] or failed["notes"] or failed["insights"]:
            status = "completed_with_issues"
        elif issues:
            status = "completed_with_issues"
        return {
            "status": status,
            "reason_code": "ok" if status == "completed" else "import_issues_present",
            "import_key": import_key,
            "registry_source_id": registry_source_id,
            "knowledge_index_id": knowledge_index_id,
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "artifact_ids": artifact_ids,
            "snapshot_ids": snapshot_ids,
            "collection_ids": sorted(set(collection_ids)),
            "issues": issues,
        }

    def _import_source(
        self,
        artifact_plan: dict[str, Any],
        *,
        sanitized_metadata: dict[str, Any],
        registry_source_id: str,
        descriptor_hash: str,
        import_key: str,
        created_by: str | None,
        collection_ids_out: list[str],
        export_version: str,
    ):
        collection_names = [str(item) for item in artifact_plan["collection_names"] if str(item).strip()]
        primary_collection = collection_names[0] if collection_names else None
        artifact, _version, collection = self._ingestion_service.upload_artifact(
            filename=str(artifact_plan["filename"]),
            content=str(artifact_plan["content"]).encode("utf-8"),
            created_by=created_by,
            media_type=str(artifact_plan["media_type"]),
            collection_name=primary_collection,
        )
        if collection is not None:
            collection_ids_out.append(str(collection.id))
        for extra_name in collection_names[1:]:
            extra = self._link_collection(artifact_id=str(artifact.id), collection_name=extra_name, created_by=created_by)
            if extra is not None:
                collection_ids_out.append(str(extra))

        metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
        metadata["ingestion_mode"] = OPEN_NOTEBOOK_INGESTION_MODE
        metadata["source_system"] = SOURCE_SYSTEM
        metadata["record_kind"] = "primary_source"
        metadata["open_notebook"] = {
            "source_id": str(artifact_plan["external_id"]),
            "notebook_ids": list(artifact_plan["notebook_ids"]),
            "import_key": import_key,
            "registry_source_id": registry_source_id,
        }
        metadata["sanitized"] = dict(sanitized_metadata or {})
        from agent.sources.open_notebook_provenance import build_open_notebook_provenance

        provenance = build_open_notebook_provenance(
            {
                **dict(sanitized_metadata or {}),
                "url": artifact_plan.get("url"),
                "file_path": artifact_plan.get("file_path"),
                "imported_at": _now_iso(),
                "export_version": export_version,
            }
        )
        metadata["provenance"] = provenance
        artifact.artifact_metadata = metadata
        artifact = self._artifact_repo.save(artifact)

        snapshot = self._snapshot_store.build_snapshot(
            source_id=registry_source_id,
            descriptor_hash=descriptor_hash,
            content_payload={
                "open_notebook_source_id": str(artifact_plan["external_id"]),
                "content": normalize_text(str(artifact_plan["content"])),
            },
            metadata_payload={
                "open_notebook_source_id": str(artifact_plan["external_id"]),
                "title": str(artifact_plan["title"]),
            },
            status="indexed",
        )
        snapshot["extensions"] = {
            "source_system": SOURCE_SYSTEM,
            "content_hash": str(artifact_plan["content_hash"]),
            "open_notebook": {
                "source_id": str(artifact_plan["external_id"]),
                "import_key": import_key,
            },
            "imported_at": _now_iso(),
            "source_title": str(artifact_plan["title"]),
            "source_url": artifact_plan.get("url"),
            "file_path": artifact_plan.get("file_path"),
            "notebook_refs": list(artifact_plan["notebook_ids"]),
            "artifact_id": str(artifact.id),
            "record_kind": "primary_source",
            "provenance": provenance,
            "citation_source": {
                "title": str(artifact_plan["title"]),
                "source_system": "OpenNotebook local/export",
                "imported_at": _now_iso(),
                "canonical_url": artifact_plan.get("url"),
            },
            "fetch_source": {
                "url": artifact_plan.get("url"),
                "file_path": artifact_plan.get("file_path"),
                "mode": "export_import",
            },
        }
        snapshot = self._snapshot_store.save_snapshot(snapshot)
        return artifact, snapshot

    def _snapshot_by_id(self, *, registry_source_id: str, snapshot_id: str) -> dict[str, Any] | None:
        if not snapshot_id:
            return None
        for snapshot in self._snapshot_store.list_snapshots(source_id=registry_source_id):
            if str(snapshot.get("snapshot_id") or "") == snapshot_id:
                return snapshot
        return None

    def _link_collection(self, *, artifact_id: str, collection_name: str, created_by: str | None):
        from agent.db_models import KnowledgeCollectionDB, KnowledgeLinkDB

        collection = self._knowledge_collection_repo.get_by_name(collection_name)
        if collection is None:
            collection = self._knowledge_collection_repo.save(KnowledgeCollectionDB(name=collection_name, created_by=created_by))
        existing = self._knowledge_link_repo.get_by_artifact(artifact_id)
        if any(str(getattr(link, "collection_id", "")) == str(collection.id) for link in existing):
            return str(collection.id)
        self._knowledge_link_repo.save(
            KnowledgeLinkDB(
                collection_id=str(collection.id),
                artifact_id=artifact_id,
                link_type="artifact",
                link_metadata={"source": OPEN_NOTEBOOK_INGESTION_MODE, "collection_name": collection_name},
            )
        )
        return str(collection.id)

    def _build_source_records(
        self,
        *,
        artifact_plan: dict[str, Any],
        snapshot: dict[str, Any],
        artifact,
        registry_source_id: str,
        import_key: str,
    ) -> list[dict[str, Any]]:
        title = str(artifact_plan["title"])
        source_hint = artifact_plan.get("url") or artifact_plan.get("file_path") or f"open-notebook/{slugify(title)}.md"
        policy_metadata = dict((getattr(artifact, "artifact_metadata", None) or {}).get("sanitized") or {})
        records: list[dict[str, Any]] = []
        for ordinal, chunk_text in enumerate(split_wiki_content(str(artifact_plan["content"]), max_chars=700), start=1):
            digest = hashlib.sha256(f"{artifact_plan['external_id']}|{chunk_text}".encode("utf-8")).hexdigest()[:16]
            records.append(
                {
                    "kind": "open_notebook_source_chunk",
                    "id": f"{slugify(str(artifact_plan['external_id']))}:{ordinal}",
                    "chunk_id": f"onb:{digest}",
                    "file": str(source_hint),
                    "title": title,
                    "topics": list(artifact_plan["topics"]),
                    "content": chunk_text,
                    "import_metadata": {
                        "source_scope": "open_notebook",
                        "source_system": SOURCE_SYSTEM,
                        "source_type": OPEN_NOTEBOOK_SOURCE_TYPE,
                        "registry_source_id": registry_source_id,
                        "open_notebook_source_id": str(artifact_plan["external_id"]),
                        "snapshot_id": str(snapshot["snapshot_id"]),
                        "artifact_id": str(artifact.id),
                        "record_kind": "primary_source",
                        "notebook_ids": list(artifact_plan["notebook_ids"]),
                        "collection_names": list(artifact_plan["collection_names"]),
                        "canonical_url": artifact_plan.get("url"),
                        "file_path": artifact_plan.get("file_path"),
                        "content_hash": str(artifact_plan["content_hash"]),
                        "import_key": import_key,
                        "source_title": title,
                        "llm_scope": str(policy_metadata.get("llm_scope") or "local_only"),
                        "sensitivity": str(policy_metadata.get("sensitivity") or "internal_high"),
                        "raw_allowed": bool(policy_metadata.get("raw_allowed", False)),
                        "source_origin": str(policy_metadata.get("source_origin") or "external_research"),
                    },
                }
            )
        return records

    def _write_knowledge_index(
        self,
        *,
        records: list[dict[str, Any]],
        import_key: str,
        registry_source_id: str,
        created_by: str | None,
    ) -> str:
        run_digest = hashlib.sha256(
            "|".join(sorted(str(record.get("chunk_id") or "") for record in records)).encode("utf-8")
        ).hexdigest()[:8]
        output_dir = self._index_root / registry_source_id / f"run-{run_digest}"
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "index.jsonl"
        index_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
            encoding="utf-8",
        )
        knowledge_index = self._knowledge_index_factory(
            source_scope="open_notebook",
            profile_name="open_notebook_import",
            status="completed",
            output_dir=str(output_dir),
            index_metadata={
                "import_key": import_key,
                "registry_source_id": registry_source_id,
                "record_count": len(records),
                "ingestion_mode": OPEN_NOTEBOOK_INGESTION_MODE,
            },
            created_by=created_by,
        )
        saved = self._knowledge_index_repo.save(knowledge_index)
        return str(getattr(saved, "id", "") or "")


def get_open_notebook_importer() -> OpenNotebookImporter:
    return OpenNotebookImporter()
