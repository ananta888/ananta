"""Passive CodeCompass projection of scientific skill supply-chain metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent.services.scientific_skill_catalog_service import (
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifest
from agent.services.scientific_skill_risk_profile_service import ScientificSkillRiskProfile


@dataclass(frozen=True)
class ScientificSkillGraphNode:
    node_id: str
    kind: str
    label: str
    attributes: dict[str, object]


@dataclass(frozen=True)
class ScientificSkillGraphEdge:
    source_id: str
    target_id: str
    relation: str


@dataclass(frozen=True)
class ScientificSkillGraphProjection:
    schema: str
    projection_digest: str
    nodes: tuple[ScientificSkillGraphNode, ...]
    edges: tuple[ScientificSkillGraphEdge, ...]
    explanation: str


class CodeCompassScientificSkillProjectionService:
    """Project metadata only; this service has no package loader or executor."""

    def project(
        self,
        *,
        manifest: ScientificSkillManifest,
        profile: ScientificSkillRiskProfile,
        catalog: ScientificSkillCatalog,
        entry: ScientificSkillCatalogEntry,
        task_id: str,
        source_id: str,
        selection_status: str,
        selection_reason: str,
        execution_receipt_digests: tuple[str, ...] = (),
    ) -> ScientificSkillGraphProjection:
        if selection_status not in {"selected", "rejected"}:
            raise ValueError("scientific_skill_selection_status_invalid")
        if entry.skill_sha256 != manifest.sha256 or entry.risk_profile_digest != profile.profile_digest:
            raise ValueError("scientific_skill_projection_binding_mismatch")
        skill_id = f"scientific-skill:{entry.entry_id}"
        catalog_id = f"scientific-skill-catalog:{catalog.catalog_digest}"
        task_node_id = f"task:{task_id}"
        source_node_id = f"source:{source_id}"
        nodes = [
            ScientificSkillGraphNode(
                skill_id,
                "scientific_skill",
                entry.skill_name,
                {
                    "upstream_repository": entry.upstream_repository,
                    "upstream_path": entry.upstream_path,
                    "upstream_pin": entry.upstream_pin,
                    "skill_sha256": entry.skill_sha256,
                    "allowed_mode": entry.allowed_mode.value,
                    "allowed_tools": entry.allowed_tools,
                    "approval_level": entry.approval_level.value,
                    "catalog_status": entry.status.value,
                    "detected_capabilities": profile.detected_capabilities,
                },
            ),
            ScientificSkillGraphNode(
                catalog_id,
                "scientific_skill_catalog",
                catalog.catalog_version,
                {"catalog_id": catalog.catalog_id, "catalog_digest": catalog.catalog_digest},
            ),
            ScientificSkillGraphNode(task_node_id, "task", task_id, {}),
            ScientificSkillGraphNode(source_node_id, "source", source_id, {}),
        ]
        edges = [
            ScientificSkillGraphEdge(skill_id, catalog_id, "governed_by"),
            ScientificSkillGraphEdge(skill_id, source_node_id, "originates_from"),
            ScientificSkillGraphEdge(skill_id, task_node_id, f"{selection_status}_for"),
        ]
        for file_metadata in manifest.declared_files:
            file_id = f"scientific-skill-file:{file_metadata.sha256}"
            nodes.append(
                ScientificSkillGraphNode(
                    file_id,
                    "scientific_skill_file",
                    file_metadata.relative_path,
                    {
                        "sha256": file_metadata.sha256,
                        "size_bytes": file_metadata.size_bytes,
                        "file_kind": file_metadata.kind,
                        "language": file_metadata.language,
                    },
                )
            )
            edges.append(ScientificSkillGraphEdge(skill_id, file_id, "declares_file"))
        for dependency in profile.dependencies:
            declaration = dependency.declaration
            dependency_id = f"scientific-skill-dependency:{_sha256(declaration)}"
            nodes.append(
                ScientificSkillGraphNode(
                    dependency_id,
                    "scientific_skill_dependency",
                    declaration,
                    {"ecosystem": dependency.ecosystem, "name": dependency.name},
                )
            )
            edges.append(ScientificSkillGraphEdge(skill_id, dependency_id, "declares_dependency"))
        for receipt_digest in sorted(execution_receipt_digests):
            if len(receipt_digest) != 64 or any(char not in "0123456789abcdef" for char in receipt_digest):
                raise ValueError("scientific_skill_receipt_digest_invalid")
            receipt_id = f"scientific-skill-receipt:{receipt_digest}"
            nodes.append(
                ScientificSkillGraphNode(receipt_id, "scientific_skill_receipt", receipt_digest[:12], {"digest": receipt_digest})
            )
            edges.append(ScientificSkillGraphEdge(receipt_id, skill_id, "receipts_execution_of"))
        nodes_tuple = tuple(sorted(nodes, key=lambda item: item.node_id))
        edges_tuple = tuple(sorted(edges, key=lambda item: (item.source_id, item.target_id, item.relation)))
        explanation = f"Skill {entry.skill_name} was {selection_status}: {selection_reason}"
        payload = {
            "schema": "ananta.codecompass.scientific-skill-projection.v1",
            "nodes": [_node_mapping(item) for item in nodes_tuple],
            "edges": [item.__dict__ for item in edges_tuple],
            "explanation": explanation,
        }
        return ScientificSkillGraphProjection(payload["schema"], _sha256(payload), nodes_tuple, edges_tuple, explanation)


def _node_mapping(node: ScientificSkillGraphNode) -> dict[str, object]:
    return {"node_id": node.node_id, "kind": node.kind, "label": node.label, "attributes": node.attributes}


def _sha256(value: object) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


__all__ = [
    "CodeCompassScientificSkillProjectionService",
    "ScientificSkillGraphEdge",
    "ScientificSkillGraphNode",
    "ScientificSkillGraphProjection",
]
