"""Hub admission of Worker CodeCompass graph and domain-supplement artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementBinding,
    CodeCompassDomainSupplementPort,
)
from agent.services.knowledge_index_worker_artifact_contract import (
    GRAPH_MEDIA_TYPES,
    OUTPUT_FILENAMES,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexArtifactTransferDeadlinePort,
)
from agent.services.knowledge_index_worker_artifact_staging import (
    file_sha256,
    strict_json_object,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    DOMAIN_SUPPLEMENT_SCHEMA,
)


class KnowledgeIndexWorkerGraphArtifactAdmission:
    """Verify staged graph artifacts and project their local Hub binding."""

    def __init__(
        self,
        *,
        domain_supplement_reader: CodeCompassDomainSupplementPort,
    ) -> None:
        self._domain_supplement_reader = domain_supplement_reader

    def validate(
        self,
        *,
        by_role: Mapping[str, Mapping[str, Any]],
        staged_paths: Mapping[str, Path],
        knowledge_index_id: str,
        source_scope: str,
        source_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> dict[str, Any]:
        graph_reference = by_role["graph_index"]
        metrics_reference = by_role["graph_visual_metrics"]
        for role, reference in (
            ("graph_index", graph_reference),
            ("graph_visual_metrics", metrics_reference),
        ):
            if (
                str(reference.get("media_type") or "").lower()
                != GRAPH_MEDIA_TYPES[role]
            ):
                raise ValueError(
                    "knowledge_index_worker_graph_artifact_media_type_invalid"
                )
        graph = strict_json_object(staged_paths["graph_index"])
        state = graph.get("state")
        if not isinstance(state, Mapping):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")
        graph_revision = str(state.get("manifest_hash") or "")
        graph_schema = str(state.get("schema") or "")
        del graph

        metrics = strict_json_object(staged_paths["graph_visual_metrics"])
        if (
            graph_schema != "codecompass_graph_index.v1"
            or str(graph_reference.get("artifact_schema") or "")
            != "codecompass_graph_index.v1"
            or str(metrics.get("schema") or "") != "graph_visual_metrics.v1"
            or str(metrics_reference.get("artifact_schema") or "")
            != "graph_visual_metrics.v1"
            or not graph_revision
            or str(metrics.get("graph_revision") or "") != graph_revision
            or str(graph_reference.get("graph_revision") or "") != graph_revision
            or str(metrics_reference.get("graph_revision") or "") != graph_revision
            or not graph_revision.startswith("sha256:")
            or len(graph_revision) != 71
        ):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")

        graph_file_hash = "sha256:" + file_sha256(staged_paths["graph_index"])
        if str(graph_reference.get("graph_content_hash") or "") != graph_file_hash:
            raise ValueError(
                "knowledge_index_worker_graph_artifact_content_hash_mismatch"
            )
        unsigned_metrics = {
            key: value for key, value in metrics.items() if key != "content_hash"
        }
        try:
            canonical_metrics = json.dumps(
                unsigned_metrics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "knowledge_index_worker_graph_visual_metrics_invalid"
            ) from exc
        metrics_content_hash = "sha256:" + hashlib.sha256(canonical_metrics).hexdigest()
        if (
            str(metrics.get("content_hash") or "") != metrics_content_hash
            or str(metrics_reference.get("graph_content_hash") or "")
            != metrics_content_hash
        ):
            raise ValueError(
                "knowledge_index_worker_graph_visual_metrics_hash_mismatch"
            )
        binding: dict[str, Any] = {
            "schema": "codecompass_graph_artifact_binding.v1",
            "graph_revision": graph_revision,
        }
        supplement_reference = by_role.get(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
        if supplement_reference is None:
            return binding
        if DOMAIN_SUPPLEMENT_OUTPUT_ROLE not in staged_paths:
            raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
        logical_content_hash = str(supplement_reference.get("graph_content_hash") or "")
        artifact_sha256 = str(supplement_reference.get("sha256") or "").lower()
        if (
            str(supplement_reference.get("media_type") or "").lower()
            != DOMAIN_SUPPLEMENT_MEDIA_TYPE
            or str(supplement_reference.get("artifact_schema") or "")
            != DOMAIN_SUPPLEMENT_SCHEMA
            or str(supplement_reference.get("graph_revision") or "") != graph_revision
            or str(supplement_reference.get("source_revision_id") or "")
            != source_revision_id
            or str(supplement_reference.get("source_revision_digest") or "")
            != source_revision_digest
            or not logical_content_hash.startswith("sha256:")
            or len(logical_content_hash) != 71
            or any(
                character not in "0123456789abcdef"
                for character in logical_content_hash[7:]
            )
            or len(artifact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in artifact_sha256)
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
        validation_options = (
            {"checkpoint": transfer_deadline.require_remaining_seconds}
            if transfer_deadline is not None
            else {}
        )
        catalog = self._domain_supplement_reader.validate_artifact(
            path=staged_paths[DOMAIN_SUPPLEMENT_OUTPUT_ROLE],
            binding=CodeCompassDomainSupplementBinding(
                knowledge_index_id=knowledge_index_id,
                source_revision_id=source_revision_id,
                source_revision_digest=source_revision_digest,
                graph_revision=graph_revision,
                artifact_sha256=artifact_sha256,
                logical_content_hash=logical_content_hash,
                source_scope=source_scope,
                source_id=source_id,
            ),
            **validation_options,
        )
        expected_counts = {
            "domain_count": len(catalog.domains),
            "semantic_node_count": sum(
                domain.semantic_node_count for domain in catalog.domains
            ),
            "semantic_edge_count": sum(
                domain.semantic_edge_count for domain in catalog.domains
            ),
            "declaration_edge_count": sum(
                domain.declaration_edge_count for domain in catalog.domains
            ),
        }
        if any(
            isinstance(supplement_reference.get(field), bool)
            or supplement_reference.get(field) != expected
            for field, expected in expected_counts.items()
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_count_mismatch")
        binding["domain_supplement"] = {
            "artifact_schema": DOMAIN_SUPPLEMENT_SCHEMA,
            "media_type": DOMAIN_SUPPLEMENT_MEDIA_TYPE,
            "graph_revision": graph_revision,
            "content_hash": logical_content_hash,
            "source_scope": source_scope,
            "source_id": source_id,
            "source_revision_id": source_revision_id,
            "source_revision_digest": source_revision_digest,
            **expected_counts,
        }
        return binding

    @staticmethod
    def local_binding(
        binding: Mapping[str, Any],
        *,
        by_role: Mapping[str, Mapping[str, Any]],
        output_dir: Path,
    ) -> dict[str, Any]:
        def artifact(role: str) -> dict[str, Any]:
            reference = by_role[role]
            projected = {
                "artifact_id": str(reference.get("artifact_id") or ""),
                "artifact_schema": str(reference.get("artifact_schema") or ""),
                "sha256": str(reference.get("sha256") or ""),
                "content_hash": str(reference.get("graph_content_hash") or ""),
                "filename": OUTPUT_FILENAMES[role],
                "local_path": str(output_dir / OUTPUT_FILENAMES[role]),
            }
            if role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE:
                projected.update(
                    {
                        "media_type": str(reference.get("media_type") or ""),
                        "graph_revision": str(reference.get("graph_revision") or ""),
                        "source_scope": str(
                            reference.get("source_scope") or "repo_path"
                        ),
                        "source_id": str(reference.get("source_id") or ""),
                        "source_revision_id": str(
                            reference.get("source_revision_id") or ""
                        ),
                        "source_revision_digest": str(
                            reference.get("source_revision_digest") or ""
                        ),
                        "domain_count": reference.get("domain_count"),
                        "semantic_node_count": reference.get("semantic_node_count"),
                        "semantic_edge_count": reference.get("semantic_edge_count"),
                        "declaration_edge_count": reference.get(
                            "declaration_edge_count"
                        ),
                    }
                )
            return projected

        local_binding = {
            **dict(binding),
            "graph_index": artifact("graph_index"),
            "visual_metrics": artifact("graph_visual_metrics"),
        }
        if DOMAIN_SUPPLEMENT_OUTPUT_ROLE in by_role:
            supplement = artifact(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
            admitted = binding.get("domain_supplement")
            if isinstance(admitted, Mapping):
                supplement.update(dict(admitted))
            local_binding["domain_supplement"] = supplement
        return local_binding


__all__ = ["KnowledgeIndexWorkerGraphArtifactAdmission"]
