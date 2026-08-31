"""Content-free Audit and CodeCompass projection for controlling findings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent.services.business_controlling_explanation import (
    EvidenceBoundedExplanation,
)
from ananta_contracts.business_controlling import (
    BusinessFinding,
    DatasetReceipt,
    ExecutionReceipt,
)


@dataclass(frozen=True)
class ControllingProvenanceProjection:
    schema_version: str
    projection_id: str
    nodes: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    audit_event: dict[str, object]
    projection_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "audit_event": self.audit_event,
            "projection_digest": self.projection_digest,
        }


class BusinessControllingProvenanceService:
    """Project identities and digests only, optionally exposing a locator."""

    def project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        dataset: DatasetReceipt,
        mapping_digest: str,
        finding: BusinessFinding,
        execution_receipt: ExecutionReceipt,
        explanation: EvidenceBoundedExplanation,
        allow_record_locator: bool,
    ) -> ControllingProvenanceProjection:
        if (
            finding.dataset_id != dataset.dataset_id
            or finding.dataset_version != dataset.dataset_version
            or finding.execution_receipt_digest
            != _digest(execution_receipt.to_dict())
            or explanation.finding_id != finding.finding_id
            or len(mapping_digest) != 64
        ):
            raise ValueError("controlling_provenance_binding_invalid")
        dataset_node_id = f"business-dataset:{dataset.dataset_id}:{dataset.dataset_version}"
        execution_node_id = f"business-execution:{execution_receipt.execution_id}"
        finding_node_id = f"business-finding:{finding.finding_id}"
        explanation_node_id = f"business-explanation:{explanation.explanation_id}"
        locator = finding.locator.to_dict() if allow_record_locator else {
            "source_kind": finding.locator.source_kind,
            "source_version": finding.locator.source_version,
            "locator_kind": finding.locator.locator_kind,
            "locator": "redacted",
        }
        nodes = (
            _node(
                dataset_node_id,
                "business_dataset",
                {
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                    "source_digest": dataset.source_digest,
                    "mapping_digest": mapping_digest,
                },
            ),
            _node(
                execution_node_id,
                "business_execution",
                {
                    "execution_id": execution_receipt.execution_id,
                    "input_digest": execution_receipt.input_digest,
                    "configuration_digest": execution_receipt.configuration_digest,
                    "output_digest": execution_receipt.output_digest,
                },
            ),
            _node(
                finding_node_id,
                "business_finding",
                {
                    "finding_id": finding.finding_id,
                    "kind": finding.kind.value,
                    "severity": finding.severity.value,
                    "rule_id": finding.rule_id,
                    "rule_version": finding.rule_version,
                    "evidence_digest": finding.evidence_digest,
                    "confidence": finding.confidence,
                    "disposition": finding.disposition.value,
                    "record_locator": locator,
                },
            ),
            _node(
                explanation_node_id,
                "business_explanation",
                {
                    "explanation_id": explanation.explanation_id,
                    "explanation_digest": explanation.explanation_digest,
                    "evidence_refs": list(explanation.evidence_refs),
                },
            ),
        )
        edges = (
            _edge(dataset_node_id, execution_node_id, "input_to"),
            _edge(execution_node_id, finding_node_id, "produced_finding"),
            _edge(finding_node_id, explanation_node_id, "explained_by"),
        )
        audit_event = {
            "event_type": "business_controlling.finding.projected",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "finding_id": finding.finding_id,
            "finding_kind": finding.kind.value,
            "disposition": finding.disposition.value,
            "mapping_digest": mapping_digest,
            "execution_receipt_digest": finding.execution_receipt_digest,
            "explanation_digest": explanation.explanation_digest,
            "locator_redacted": not allow_record_locator,
        }
        material = {
            "nodes": nodes,
            "edges": edges,
            "audit_event": audit_event,
        }
        digest = _digest(material)
        return ControllingProvenanceProjection(
            schema_version="ananta.business-controlling-provenance.v1",
            projection_id=f"ctrlprojection_{digest}",
            nodes=nodes,
            edges=edges,
            audit_event=audit_event,
            projection_digest=digest,
        )


def _node(
    node_id: str,
    kind: str,
    attributes: dict[str, object],
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "node_type": kind,
        "attributes": {
            "name": node_id,
            "content": "",
            "record_id": node_id,
            **attributes,
        },
    }


def _edge(source_id: str, target_id: str, relation: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
    }


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingProvenanceService",
    "ControllingProvenanceProjection",
]
