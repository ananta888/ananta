from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk
from agent.repository import knowledge_index_repo, knowledge_link_repo
from agent.services.knowledge_index_consumption_policy import (
    KnowledgeIndexConsumptionPolicy,
    get_knowledge_index_consumption_policy,
)
from agent.services.knowledge_index_manifest_identity import (
    KnowledgeIndexManifestIdentityResolver,
    KnowledgeIndexManifestIdentitySource,
)
from agent.services.knowledge_index_record_loading import (
    KnowledgeIndexRecordReader,
    KnowledgeIndexRecordSource,
    nested_metadata_value,
)
from agent.services.knowledge_index_record_loading import _is_sha256 as _is_sha256
from agent.services.knowledge_index_record_scoring import (
    KnowledgeIndexRecordRanking,
    KnowledgeIndexRecordScorer,
)
from agent.services.knowledge_index_result_projection import (
    KnowledgeIndexResultProjecting,
    KnowledgeIndexResultProjector,
)
from agent.services.knowledge_index_result_projection import (
    _strip_index_header as _strip_index_header,
)
from agent.services.retrieval_source_contract import normalize_chunk_metadata
from ananta_contracts.file_type_classifier import FileTypeClassifier
from ananta_contracts.file_type_support import (
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)


class KnowledgeIndexRetrievalService:
    """Reads completed rag-helper outputs as an additive retrieval source.

    Orchestrates index selection and candidate ranking. It composes four
    collaborators behind narrow protocols (record reading, record ranking,
    manifest identity, result projection); each is built here with its
    production default and can be replaced through a keyword-only parameter.
    """

    OUTPUT_FILENAMES = KnowledgeIndexRecordReader.OUTPUT_FILENAMES

    def __init__(
        self,
        knowledge_index_repository=None,
        knowledge_link_repository=None,
        *,
        file_type_registry: FileTypeSupportRegistry | None = None,
        consumption_policy: KnowledgeIndexConsumptionPolicy | None = None,
        record_source: KnowledgeIndexRecordSource | None = None,
        record_ranking: KnowledgeIndexRecordRanking | None = None,
        manifest_identity: KnowledgeIndexManifestIdentitySource | None = None,
        result_projector: KnowledgeIndexResultProjecting | None = None,
    ) -> None:
        self._knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
        self._knowledge_link_repository = knowledge_link_repository or knowledge_link_repo
        self._consumption_policy = (
            consumption_policy or get_knowledge_index_consumption_policy()
        )
        self._records: KnowledgeIndexRecordSource = record_source or KnowledgeIndexRecordReader()
        if record_ranking is None:
            registry = file_type_registry or load_file_type_support_registry(
                Path(__file__).resolve().parents[2]
            )
            record_ranking = KnowledgeIndexRecordScorer(FileTypeClassifier(registry))
        self._ranking: KnowledgeIndexRecordRanking = record_ranking
        self._manifest_identity: KnowledgeIndexManifestIdentitySource = (
            manifest_identity or KnowledgeIndexManifestIdentityResolver(self._iter_completed_indices)
        )
        self._projector: KnowledgeIndexResultProjecting = result_projector or KnowledgeIndexResultProjector()

    def load_bound_records(
        self,
        *,
        knowledge_index: Any,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Hydrate exact, hash-verified records (see ``KnowledgeIndexRecordReader``)."""
        return self._records.load_bound_records(knowledge_index=knowledge_index, bindings=bindings)

    def current_manifest_identity(self) -> dict[str, Any]:
        """Return the newest immutable snapshot revision, never a host-path hash."""
        return self._manifest_identity.current_manifest_identity()

    def _collection_metadata(self, artifact_id: str) -> tuple[list[str], list[str]]:
        if not artifact_id:
            return [], []
        links = self._knowledge_link_repository.get_by_artifact(artifact_id)
        collection_ids: list[str] = []
        collection_names: list[str] = []
        for link in links:
            collection_id = str(getattr(link, "collection_id", "") or "").strip()
            collection_name = str(((getattr(link, "link_metadata", None) or {}).get("collection_name")) or "").strip()
            if collection_id and collection_id not in collection_ids:
                collection_ids.append(collection_id)
            if collection_name and collection_name not in collection_names:
                collection_names.append(collection_name)
        return collection_ids, collection_names

    def _iter_completed_indices(
        self,
        *,
        allowed_index_ids: set[str] | None = None,
    ):
        for knowledge_index in self._knowledge_index_repository.list_completed():
            if self._consumption_policy.can_consume(
                knowledge_index,
                allowed_index_ids=allowed_index_ids,
            ):
                yield knowledge_index

    def get_source_preflight(self) -> dict[str, object]:
        root = Path(settings.data_dir) / "knowledge_indices"
        by_scope: dict[str, dict[str, object]] = {
            "artifact": {
                "status": "degraded",
                "completed_indices": 0,
                "issues": [],
                "storage_root": str((root / "artifact").resolve()),
            },
            "wiki": {
                "status": "degraded",
                "completed_indices": 0,
                "issues": [],
                "storage_root": str((root / "wiki").resolve()),
            },
        }
        for knowledge_index in self._iter_completed_indices():
            scope = (
                str(getattr(knowledge_index, "source_scope", "artifact") or "artifact")
                .strip()
                .lower()
                or "artifact"
            )
            if scope not in by_scope:
                by_scope[scope] = {
                    "status": "degraded",
                    "completed_indices": 0,
                    "issues": [],
                    "storage_root": str((root / scope).resolve()),
                }
            bucket = by_scope[scope]
            bucket["completed_indices"] = int(bucket.get("completed_indices") or 0) + 1

            output_dir_raw = getattr(knowledge_index, "output_dir", None)
            if not output_dir_raw:
                issues = list(bucket.get("issues") or [])
                issues.append("missing_output_dir")
                bucket["issues"] = issues
                continue
            output_dir = Path(output_dir_raw)
            if not output_dir.exists() or not output_dir.is_dir():
                issues = list(bucket.get("issues") or [])
                issues.append("output_dir_missing")
                bucket["issues"] = issues
                continue
            if not any((output_dir / name).exists() for name in self._records.OUTPUT_FILENAMES):
                issues = list(bucket.get("issues") or [])
                issues.append("no_index_outputs")
                bucket["issues"] = issues

        for scope, bucket in by_scope.items():
            issues = list(bucket.get("issues") or [])
            completed = int(bucket.get("completed_indices") or 0)
            if completed <= 0:
                bucket["status"] = "degraded"
                issues.append("no_completed_indices")
            elif issues:
                bucket["status"] = "degraded"
            else:
                bucket["status"] = "ok"
            # de-duplicate while keeping order
            seen: set[str] = set()
            unique_issues: list[str] = []
            for issue in issues:
                if issue not in seen:
                    seen.add(issue)
                    unique_issues.append(issue)
            bucket["issues"] = unique_issues
            by_scope[scope] = bucket
        return by_scope

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        artifact_ids: set[str] | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
        record_predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[ContextChunk]:
        ranked = self._ranked_candidates(
            query,
            artifact_ids=artifact_ids,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes=source_scopes,
            allowed_index_ids=allowed_index_ids,
            authoritative_scope=authoritative_scope,
            record_predicate=record_predicate,
        )
        return [chunk for chunk, _record in ranked[: max(1, int(top_k))]]

    def _ranked_candidates(
        self,
        query: str,
        *,
        artifact_ids: set[str] | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
        record_predicate: Callable[[dict[str, Any]], bool] | None = None,
        index_ids: set[str] | None = None,
        unscored_matches: list[int] | None = None,
    ) -> list[tuple[ContextChunk, dict[str, Any]]]:
        """Every scoring candidate with its raw record, best first (``search`` keeps the head).

        ``index_ids`` is a strict allow-list applied to every index, legacy ones
        included, on top of the consumption policy's ``allowed_index_ids``.
        """
        query_features = self._ranking.query_features(query)
        profile = self._ranking.task_profile(task_kind, retrieval_intent)
        candidates: list[tuple[ContextChunk, dict[str, Any]]] = []
        for knowledge_index in self._iter_completed_indices(
            allowed_index_ids=allowed_index_ids,
        ):
            if index_ids is not None and str(getattr(knowledge_index, "id", "")) not in index_ids:
                continue
            expected_scope = dict(authoritative_scope or {})
            observed_scope = {
                "tenant_id": getattr(knowledge_index, "tenant_id", ""),
                "workspace_id": getattr(knowledge_index, "workspace_id", ""),
                "repository_id": getattr(knowledge_index, "repository_id", ""),
                "revision": (
                    getattr(knowledge_index, "revision", "")
                    or getattr(knowledge_index, "source_revision", "")
                ),
            }
            if any(
                str(observed_scope.get(field) or "")
                and str(observed_scope.get(field)) != str(expected)
                for field, expected in expected_scope.items()
                if field in observed_scope and str(expected or "")
            ):
                continue
            source_scope = (
                str(getattr(knowledge_index, "source_scope", "artifact") or "artifact")
                .strip()
                .lower()
                or "artifact"
            )
            if source_scopes is not None and source_scope not in source_scopes:
                continue
            artifact_id = str(getattr(knowledge_index, "artifact_id", "") or "")
            if artifact_ids is not None and (
                source_scope != "artifact"
                or artifact_id not in artifact_ids
            ):
                # An artifact allow-list is a strict capability boundary. It
                # must never degrade into a filter that admits every other
                # source scope (repo, wiki, registered workspace, ...).
                continue
            collection_ids, collection_names = self._collection_metadata(artifact_id)
            layered = self._layered_candidates(knowledge_index, query)
            if layered is not None:
                # A layer-head pointer: the FTS projection preselects the chunks to score.
                output_records, unscored = layered
                if unscored_matches is not None:
                    unscored_matches.append(unscored)
            else:
                output_dir_raw = getattr(knowledge_index, "output_dir", None)
                if not output_dir_raw:
                    continue
                output_dir = Path(output_dir_raw)
                if not output_dir.exists():
                    continue
                output_records = list(self._records.iter_output_records(output_dir))
            duplicate_ids = self._ranking.duplicate_candidate_ids(output_records)
            for filename, record in output_records:
                if record_predicate is not None and not record_predicate(record):
                    continue
                display_path = self._records.display_path(record)
                source = str(
                    display_path
                    or record.get("id")
                    or artifact_id
                    or "knowledge-index"
                )
                record_text = self._records.record_text(record)
                if not record_text:
                    continue
                record_kind = str(record.get("kind", ""))
                field_texts = self._records.record_field_texts(record, source)
                score, breakdown = self._ranking.score_record(
                    query=query,
                    record=record,
                    query_features=query_features,
                    field_texts=field_texts,
                    record_kind=record_kind,
                    source_hint=source,
                    profile=profile,
                    duplicate_ids=duplicate_ids,
                )
                if score <= 0:
                    continue
                raw_metadata = {
                    "knowledge_index_id": str(getattr(knowledge_index, "id", "")),
                    "artifact_id": artifact_id,
                    "record_kind": record_kind,
                    "record_file": filename,
                    "record_id": str(record.get("id") or "").strip() or None,
                    "source_scope": source_scope,
                    "profile_name": str(getattr(knowledge_index, "profile_name", "default")),
                    "collection_ids": collection_ids,
                    "collection_names": collection_names,
                    "retrieval_score_breakdown": breakdown,
                    "task_kind": str(task_kind or "").strip() or None,
                    "retrieval_intent": str(retrieval_intent or "").strip() or None,
                    "importance_score": record.get("importance_score"),
                    "generated_code": bool(record.get("generated_code", False)),
                    "duplicate_candidate": bool(str(record.get("id") or "").strip() in duplicate_ids),
                    "boilerplate_candidate": self._ranking.is_boilerplate_candidate(
                        record,
                        source_hint=source,
                        record_kind=record_kind,
                    ),
                    "article_title": str(record.get("article_title") or record.get("title") or "").strip() or None,
                    "section_title": str(record.get("section_title") or record.get("heading") or "").strip() or None,
                    "language": str(record.get("language") or record.get("lang") or "").strip() or None,
                    "wiki_article_id": str(record.get("wiki_article_id") or "").strip() or None,
                    "revision": str(record.get("revision") or record.get("revision_id") or "").strip() or None,
                    "import_revision": str(record.get("import_revision") or "").strip() or None,
                    "import_metadata": dict(record.get("import_metadata") or {}),
                    # CCSH-010: line-range metadata — use from record if present, else mark unknown
                    "start_line": record.get("start_line"),
                    "end_line": record.get("end_line"),
                    "line_range_status": "available" if (
                        record.get("start_line") is not None and record.get("end_line") is not None
                    ) else "unknown",
                    "repo_relative_path": str(record.get("path") or record.get("file") or "").strip() or None,
                    # Citable file path, also when the record keeps it only in
                    # ``metadata.relative_path`` (records ingestion). Display
                    # only: ``repo_relative_path`` stays the hydration locator.
                    "display_path": display_path or None,
                    "symbol": (
                        str(record.get("symbol") or nested_metadata_value(record, "symbol") or "").strip() or None
                    ),
                    # Provider-issued identity remains unverified here and is
                    # released only after SourceCatalogAuthority validation.
                    "source_id": str(record.get("source_id") or "").strip() or None,
                    "source_version": str(record.get("source_version") or "").strip() or None,
                    "tenant_id": str(record.get("tenant_id") or "").strip() or None,
                    "scope": str(record.get("scope") or record.get("source_scope") or "").strip() or None,
                    "provenance_digest": str(record.get("provenance_digest") or "").strip() or None,
                    "source_provenance": dict(record.get("provenance") or {}),
                    "content_hash": str(record.get("content_hash") or "").strip() or None,
                    "source_manifest_hash": str(record.get("manifest_hash") or "").strip() or None,
                    "line_start": record.get("line_start", record.get("start_line")),
                    "line_end": record.get("line_end", record.get("end_line")),
                }
                candidates.append((
                    ContextChunk(
                        engine="knowledge_index",
                        source=source,
                        content=record_text,
                        score=score,
                        metadata=normalize_chunk_metadata(
                            engine="knowledge_index",
                            source=source,
                            content=record_text,
                            metadata=raw_metadata,
                            verified_source_ids=(),
                        ),
                    ),
                    record,
                ))
        return sorted(
            candidates,
            key=lambda item: (-item[0].score, item[0].engine, item[0].source, item[0].content[:80]),
        )

    def search_records(
        self,
        query: str,
        *,
        limit: int = 8,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Stable dict projection for CodeCompass tools and context planners."""

        chunks = self.search(
            query,
            top_k=max(1, int(limit)),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes=source_scopes,
            allowed_index_ids=allowed_index_ids,
            authoritative_scope=authoritative_scope,
        )
        records = (self._projector.record_projection(chunk, authoritative_scope) for chunk in chunks)
        return [record for record in records if record is not None]

    def search_records_page(
        self,
        query: str,
        *,
        limit: int = 8,
        passage_chars: int | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
        index_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """``search_records`` plus the total hit count and, optionally, a matching passage.

        ``total`` counts every scoring record inside the scope, not only the
        returned head. With ``passage_chars`` each record carries
        ``passage = {text, line_start, line_end}``: the window of its raw
        content that matches the query, instead of the record's beginning.
        """
        unscored: list[int] = []
        ranked = self._ranked_candidates(
            query,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes=source_scopes,
            allowed_index_ids=allowed_index_ids,
            authoritative_scope=authoritative_scope,
            index_ids=index_ids,
            unscored_matches=unscored,
        )
        records: list[dict[str, Any]] = []
        total = sum(unscored)
        for chunk, raw_record in ranked:
            record = self._projector.record_projection(chunk, authoritative_scope)
            if record is None:
                continue
            total += 1
            if len(records) >= max(1, int(limit)):
                continue
            if passage_chars:
                record["passage"] = self._projector.passage(raw_record, query, int(passage_chars))
            records.append(record)
        return {"records": records, "total": total}

    LAYER_CANDIDATES = 400

    def _layered_candidates(
        self, knowledge_index: Any, query: str
    ) -> tuple[list[tuple[str, dict[str, Any]]], int] | None:
        """``(records, unscored match count)`` for a layer-head pointer index, else ``None``."""
        from agent.services.codecompass_layer_record_source import layer_record_source

        source = layer_record_source(knowledge_index)
        if source is None:
            return None
        records, total = source.candidates(query, limit=self.LAYER_CANDIDATES)
        return [("layer:chunks", record) for record in records], max(0, total - len(records))


knowledge_index_retrieval_service = KnowledgeIndexRetrievalService()


def get_knowledge_index_retrieval_service() -> KnowledgeIndexRetrievalService:
    return knowledge_index_retrieval_service
