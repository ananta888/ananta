from __future__ import annotations

import hashlib
import json

from agent.services.business_controlling_explanation import (
    BusinessControllingExplanationService,
    EvidenceStatement,
    EvidenceStatementKind,
)
from agent.services.business_controlling_provenance import (
    BusinessControllingProvenanceService,
)
from agent.services.codecompass_graph_projection_service import (
    CodeCompassGraphProjectionService,
)
from ananta_contracts.business_controlling import (
    CONTRACT_VERSION,
    BusinessFinding,
    DatasetReceipt,
    ExecutionReceipt,
    FindingDisposition,
    FindingKind,
    FindingSeverity,
    RecordLocator,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_projection_is_content_free_redacted_and_codecompass_known() -> None:
    dataset = DatasetReceipt.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "dataset_id": "dataset-a",
            "dataset_version": "version-a",
            "source_digest": "1" * 64,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "currency": "EUR",
            "column_mapping": {"amount": "amount"},
        }
    )
    receipt = ExecutionReceipt("execution-a", "2" * 64, "3" * 64, "4" * 64)
    finding = BusinessFinding(
        CONTRACT_VERSION,
        "finding-a",
        "dataset-a",
        "version-a",
        FindingKind.DETERMINISTIC_VIOLATION,
        FindingSeverity.HIGH,
        "required-field",
        "v1",
        RecordLocator("csv", "version-a", "csv_row", "row-secret"),
        "5" * 64,
        None,
        FindingDisposition.CONFIRMED,
        _digest(receipt.to_dict()),
    )
    explanation = BusinessControllingExplanationService().explain(
        finding=finding,
        statements=(
            EvidenceStatement(
                EvidenceStatementKind.RULE,
                "required-field",
                "5" * 64,
                "Required mapped field was absent.",
            ),
        ),
    )

    projection = BusinessControllingProvenanceService().project(
        tenant_id="tenant-a",
        project_id="project-a",
        dataset=dataset,
        mapping_digest="6" * 64,
        finding=finding,
        execution_receipt=receipt,
        explanation=explanation,
        allow_record_locator=False,
    )

    encoded = json.dumps(projection.to_dict(), sort_keys=True)
    assert "row-secret" not in encoded
    assert '"locator": "redacted"' in encoded
    graph = CodeCompassGraphProjectionService().project(
        nodes=projection.nodes,
        edges=projection.edges,
        source_kind="business_controlling",
        source_ref=projection.projection_id,
    )
    assert {node["node_type"] for node in graph["nodes"]} == {
        "business_dataset",
        "business_execution",
        "business_explanation",
        "business_finding",
    }
    assert {edge["relation"] for edge in graph["edges"]} == {
        "explained_by",
        "input_to",
        "produced_finding",
    }
