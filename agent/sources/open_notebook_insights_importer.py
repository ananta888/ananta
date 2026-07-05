from __future__ import annotations

import hashlib
from typing import Any

from agent.services.wiki_chunking_policy import split_wiki_content
from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy
from agent.sources.open_notebook_mapper import slugify

INSIGHT_RECORD_KIND = "source_insight"


class OpenNotebookInsightsImporter:
    """Imports OpenNotebook source insights as derived artifacts.

    Insights keep parent_source_ref and transformation metadata so retrieval
    can always present them as 'derived insight', never as a primary source.
    Insights are not deduplicated against their parent sources.
    """

    def __init__(self, *, ingestion_service, artifact_repository, policy: OpenNotebookImportPolicy | None = None) -> None:
        self._ingestion_service = ingestion_service
        self._artifact_repo = artifact_repository
        self._policy = policy or OpenNotebookImportPolicy()

    def import_insights(
        self,
        insights: list[dict[str, Any]],
        *,
        import_key: str,
        registry_source_id: str,
        snapshots_by_source: dict[str, dict[str, Any]],
        artifacts_by_source: dict[str, Any],
        created_by: str | None = None,
    ) -> dict[str, Any]:
        imported = 0
        skipped = 0
        failed = 0
        issues: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        artifact_ids: list[str] = []

        for insight in insights:
            insight_id = str(insight.get("id") or "").strip()
            parent_source_id = str(insight.get("source_id") or "").strip()
            decision = self._policy.evaluate_record(insight, section="source_insights")
            if not decision.allowed:
                skipped += 1
                issues.append({"reason_code": decision.reason_code, "insight_id": insight_id})
                continue
            parent_snapshot = snapshots_by_source.get(parent_source_id)
            parent_artifact = artifacts_by_source.get(parent_source_id)
            if parent_snapshot is None or parent_artifact is None:
                skipped += 1
                issues.append(
                    {
                        "reason_code": "insight_missing_parent_source",
                        "insight_id": insight_id,
                        "parent_source_id": parent_source_id,
                    }
                )
                continue
            content = str(insight.get("content") or "").strip()
            transformation_name = str(insight.get("transformation_name") or "") or None
            insight_type = str(insight.get("insight_type") or "") or None
            title = transformation_name or insight_type or f"Insight {insight_id}"
            try:
                artifact, _version, _collection = self._ingestion_service.upload_artifact(
                    filename=f"insight-{slugify(insight_id, fallback='insight')}.md",
                    content=f"# {title}\n\n{content}\n".encode("utf-8"),
                    created_by=created_by,
                    media_type="text/markdown",
                    collection_name=None,
                )
                metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
                metadata["ingestion_mode"] = "open_notebook_import"
                metadata["source_system"] = "open_notebook"
                metadata["record_kind"] = INSIGHT_RECORD_KIND
                metadata["derived_from"] = "open_notebook"
                metadata["transformation_name"] = transformation_name
                metadata["insight_type"] = insight_type
                metadata["parent_source_ref"] = {
                    "open_notebook_source_id": parent_source_id,
                    "artifact_id": str(parent_artifact.id),
                    "snapshot_id": str(parent_snapshot.get("snapshot_id") or ""),
                }
                metadata["open_notebook"] = {
                    "insight_id": insight_id,
                    "source_id": parent_source_id,
                    "import_key": import_key,
                    "registry_source_id": registry_source_id,
                }
                metadata["sanitized"] = dict(decision.sanitized_metadata or {})
                artifact.artifact_metadata = metadata
                artifact = self._artifact_repo.save(artifact)
            except Exception as exc:  # noqa: BLE001 - collected as import issue
                failed += 1
                issues.append({"reason_code": "insight_import_failed", "insight_id": insight_id, "detail": str(exc)})
                continue
            imported += 1
            artifact_ids.append(str(artifact.id))
            for ordinal, chunk_text in enumerate(split_wiki_content(content, max_chars=700), start=1):
                digest = hashlib.sha256(f"insight|{insight_id}|{chunk_text}".encode("utf-8")).hexdigest()[:16]
                records.append(
                    {
                        "kind": "open_notebook_insight_chunk",
                        "id": f"insight-{slugify(insight_id)}:{ordinal}",
                        "chunk_id": f"onb-insight:{digest}",
                        "file": f"open-notebook/insights/{slugify(insight_id)}.md",
                        "title": title,
                        "content": chunk_text,
                        "import_metadata": {
                            "source_scope": "open_notebook",
                            "source_system": "open_notebook",
                            "source_type": "open_notebook",
                            "registry_source_id": registry_source_id,
                            "open_notebook_insight_id": insight_id,
                            "artifact_id": str(artifact.id),
                            "record_kind": INSIGHT_RECORD_KIND,
                            "derived_from": "open_notebook",
                            "parent_source_id": parent_source_id,
                            "parent_artifact_id": str(parent_artifact.id),
                            "parent_source_snapshot_id": str(parent_snapshot.get("snapshot_id") or ""),
                            "transformation_name": transformation_name,
                            "insight_type": insight_type,
                            "import_key": import_key,
                            "source_title": title,
                        },
                    }
                )

        return {
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "issues": issues,
            "records": records,
            "artifact_ids": artifact_ids,
        }
