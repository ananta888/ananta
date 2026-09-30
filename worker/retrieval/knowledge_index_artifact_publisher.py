"""Publish worker knowledge-index output files as revision-bound artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from worker.retrieval.knowledge_index_execution_guard import (
    KnowledgeIndexExecutionDeadlinePort,
)


class WorkerKnowledgeIndexArtifactPublisher:
    """Publish real worker output files through the existing artifact API."""

    _OUTPUTS = {
        "manifest": ("manifest.json", "application/json"),
        "index": ("index.jsonl", "application/x-ndjson"),
        "details": ("details.jsonl", "application/x-ndjson"),
        "relations": ("relations.jsonl", "application/x-ndjson"),
        "graph_index": (
            "cc_graph_index.json",
            "application/vnd.ananta.codecompass-graph-index+json",
        ),
        "graph_visual_metrics": (
            "cc_graph_index.visual_metrics.json",
            "application/vnd.ananta.codecompass-graph-visual-metrics+json",
        ),
        DOMAIN_SUPPLEMENT_OUTPUT_ROLE: (
            DOMAIN_SUPPLEMENT_FILENAME,
            DOMAIN_SUPPLEMENT_MEDIA_TYPE,
        ),
    }
    _MAX_OUTPUT_BYTES = 128 * 1024 * 1024
    _MAX_GRAPH_OUTPUT_BYTES = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES
    _GRAPH_OUTPUT_ROLES = frozenset({"graph_index", "graph_visual_metrics"})
    _REVISION_BOUND_OUTPUT_ROLES = _GRAPH_OUTPUT_ROLES | frozenset(
        {DOMAIN_SUPPLEMENT_OUTPUT_ROLE}
    )
    _READ_CHUNK_BYTES = 1024 * 1024

    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]:
        from agent.repository import artifact_repo
        from agent.services.ingestion_service import get_ingestion_service

        self._checkpoint(execution_deadline)
        output_dir_value = str(run.get("output_dir") or knowledge_index.get("output_dir") or "").strip()
        if not output_dir_value:
            raise RuntimeError("knowledge_index_output_directory_missing")
        output_dir = Path(output_dir_value)
        if output_dir.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        try:
            resolved_output = output_dir.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("knowledge_index_output_directory_missing") from exc
        if not resolved_output.is_dir() or resolved_output.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        graph_files = (
            resolved_output / "cc_graph_index.json",
            resolved_output / "cc_graph_index.visual_metrics.json",
        )
        graph_file_presence = tuple(path.exists() for path in graph_files)
        supplement_path = resolved_output / DOMAIN_SUPPLEMENT_FILENAME
        supplement_present = supplement_path.exists()
        if supplement_present and revision_binding is None:
            raise RuntimeError("knowledge_index_revision_binding_missing")
        if any(graph_file_presence) and not all(graph_file_presence):
            raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
        if supplement_present and not all(graph_file_presence):
            raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
        if all(graph_file_presence):
            for graph_file in graph_files:
                if graph_file.is_symlink() or not graph_file.is_file():
                    raise RuntimeError("knowledge_index_output_artifact_invalid")
                if graph_file.stat().st_size > self._MAX_GRAPH_OUTPUT_BYTES:
                    raise RuntimeError("knowledge_index_graph_artifact_too_large")
            graph_binding = self._load_graph_binding(
                resolved_output,
                knowledge_index=knowledge_index,
                revision_binding=revision_binding,
                include_domain_supplement=supplement_present,
                execution_deadline=execution_deadline,
            )
        else:
            graph_binding = {}
        references: list[dict[str, Any]] = []
        for role, (filename, media_type) in self._OUTPUTS.items():
            self._checkpoint(execution_deadline)
            if role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE and not supplement_present:
                continue
            path = resolved_output / filename
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("knowledge_index_output_artifact_invalid")
            size_bytes = path.stat().st_size
            if (
                role in self._GRAPH_OUTPUT_ROLES
                and size_bytes > self._MAX_GRAPH_OUTPUT_BYTES
            ):
                raise RuntimeError("knowledge_index_graph_artifact_too_large")
            if size_bytes < 0 or size_bytes > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("knowledge_index_output_artifact_too_large")
            content = self._read_bounded_file(
                path,
                expected_size=size_bytes,
                execution_deadline=execution_deadline,
            )
            if len(content) != size_bytes:
                raise RuntimeError("knowledge_index_output_artifact_size_mismatch")
            upload_kwargs = (
                {"execution_checkpoint": execution_deadline.checkpoint}
                if execution_deadline is not None
                else {}
            )
            initial_artifact_metadata = {
                "system_artifact_kind": "knowledge_index_worker_output",
                "knowledge_index_job_id": job_id,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "knowledge_index_run_id": str(run.get("id") or ""),
                "output_role": role,
                **(
                    graph_binding.get(role, {})
                    if role in self._REVISION_BOUND_OUTPUT_ROLES
                    else {}
                ),
            }
            artifact, version, _collection = get_ingestion_service().upload_artifact(
                filename=f"{job_id}-{run.get('id')}-{filename}",
                content=content,
                created_by="knowledge-index-worker",
                media_type=media_type,
                artifact_metadata=initial_artifact_metadata,
                **upload_kwargs,
            )
            self._checkpoint(execution_deadline)
            artifact.artifact_metadata = {
                **dict(artifact.artifact_metadata or {}),
                **initial_artifact_metadata,
            }
            reference = {
                "artifact_id": artifact.id,
                "sha256": version.sha256,
                "media_type": version.media_type,
                "role": role,
                "filename": filename,
                "size_bytes": version.size_bytes,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "run_id": str(run.get("id") or ""),
            }
            if role in self._REVISION_BOUND_OUTPUT_ROLES:
                if role not in graph_binding:
                    raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
                reference.update(graph_binding[role])
                artifact.artifact_metadata = {
                    **dict(artifact.artifact_metadata or {}),
                    **graph_binding[role],
                }
            artifact_repo.save(artifact)
            references.append(reference)
        self._checkpoint(execution_deadline)
        return references

    @classmethod
    def _load_graph_binding(
        cls,
        output_dir: Path,
        *,
        knowledge_index: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None,
        include_domain_supplement: bool,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> dict[str, dict[str, Any]]:
        graph_path = output_dir / "cc_graph_index.json"
        metrics_path = output_dir / "cc_graph_index.visual_metrics.json"
        try:
            graph_bytes = cls._read_bounded_file(
                graph_path,
                expected_size=graph_path.stat().st_size,
                execution_deadline=execution_deadline,
            )
            metrics_bytes = cls._read_bounded_file(
                metrics_path,
                expected_size=metrics_path.stat().st_size,
                execution_deadline=execution_deadline,
            )
            graph = json.loads(graph_bytes)
            metrics = json.loads(metrics_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("knowledge_index_graph_artifacts_invalid") from exc
        if not isinstance(graph, dict) or not isinstance(metrics, dict):
            raise RuntimeError("knowledge_index_graph_artifacts_invalid")
        state = graph.get("state")
        if not isinstance(state, Mapping):
            raise RuntimeError("knowledge_index_graph_artifact_revision_mismatch")
        graph_revision = str(state.get("manifest_hash") or "").strip()
        if (
            str(state.get("schema") or "") != "codecompass_graph_index.v1"
            or str(metrics.get("schema") or "") != "graph_visual_metrics.v1"
            or str(metrics.get("graph_revision") or "") != graph_revision
            or not graph_revision
            or not graph_revision.startswith("sha256:")
            or len(graph_revision) != 71
        ):
            raise RuntimeError("knowledge_index_graph_artifact_revision_mismatch")
        from worker.retrieval.codecompass_graph_visual_metrics import (
            verify_visual_metrics_content_hash,
        )

        if not verify_visual_metrics_content_hash(metrics):
            raise RuntimeError("knowledge_index_graph_visual_metrics_hash_invalid")
        binding: dict[str, dict[str, Any]] = {
            "graph_index": {
                "artifact_schema": "codecompass_graph_index.v1",
                "graph_revision": graph_revision,
                "graph_content_hash": "sha256:"
                + hashlib.sha256(graph_bytes).hexdigest(),
            },
            "graph_visual_metrics": {
                "artifact_schema": "graph_visual_metrics.v1",
                "graph_revision": graph_revision,
                "graph_content_hash": str(metrics.get("content_hash") or ""),
            },
        }
        if include_domain_supplement:
            if revision_binding is None:
                raise RuntimeError("knowledge_index_revision_binding_missing")
            from worker.retrieval.codecompass_domain_supplement import (
                WorkerCodeCompassDomainSupplementMaterializer,
            )

            try:
                supplement = (
                    WorkerCodeCompassDomainSupplementMaterializer.inspect_published(
                        output_dir / DOMAIN_SUPPLEMENT_FILENAME,
                        execution_deadline=execution_deadline,
                    )
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "knowledge_index_domain_supplement_invalid"
                ) from exc
            expected_supplement = {
                "graph_revision": graph_revision,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "source_scope": str(
                    revision_binding.get("source_scope") or ""
                ),
                "source_id": str(revision_binding.get("source_id") or ""),
                "source_revision_id": str(
                    revision_binding.get("source_revision_id") or ""
                ),
                "source_revision_digest": str(
                    revision_binding.get("source_revision_digest") or ""
                ),
            }
            if any(
                str(supplement.get(field) or "") != expected
                for field, expected in expected_supplement.items()
            ):
                raise RuntimeError(
                    "knowledge_index_domain_supplement_binding_mismatch"
                )
            binding[DOMAIN_SUPPLEMENT_OUTPUT_ROLE] = {
                key: value
                for key, value in supplement.items()
                if key
                in {
                    "artifact_schema",
                    "graph_revision",
                    "graph_content_hash",
                    "source_revision_id",
                    "source_revision_digest",
                    "domain_count",
                    "semantic_node_count",
                    "semantic_edge_count",
                    "declaration_edge_count",
                }
            }
        return binding

    @staticmethod
    def _checkpoint(
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> None:
        if execution_deadline is not None:
            execution_deadline.checkpoint()

    @classmethod
    def _read_bounded_file(
        cls,
        path: Path,
        *,
        expected_size: int,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> bytes:
        chunks: list[bytes] = []
        received = 0
        with path.open("rb") as handle:
            while True:
                cls._checkpoint(execution_deadline)
                chunk = handle.read(cls._READ_CHUNK_BYTES)
                cls._checkpoint(execution_deadline)
                if not chunk:
                    break
                received += len(chunk)
                if received > expected_size:
                    raise RuntimeError(
                        "knowledge_index_output_artifact_size_mismatch"
                    )
                chunks.append(chunk)
        if received != expected_size:
            raise RuntimeError(
                "knowledge_index_output_artifact_size_mismatch"
            )
        return b"".join(chunks)


__all__ = ["WorkerKnowledgeIndexArtifactPublisher"]
