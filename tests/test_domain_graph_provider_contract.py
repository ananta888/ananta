from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.providers.domain_graph import DomainGraphIngestRequest, DomainGraphIngestResult
from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "artifacts" / "domain_graph_artifact.v1.json"
MODULE_PATH = ROOT / "agent" / "providers" / "domain_graph.py"


class _MockDomainGraphProvider:
    descriptor = ProviderDescriptor(
        provider_id="mock_domain_graph",
        provider_family="domain_graph",
        capabilities=("ingest",),
        risk_class="low",
        enabled_by_default=False,
    )

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(status="healthy")

    def ingest(self, request: DomainGraphIngestRequest) -> DomainGraphIngestResult:
        artifact = {
            "schema": "domain_graph_artifact.v1",
            "source_kind": request.source_kind,
            "source_ref": request.source_ref,
            "nodes": [{"node_id": "n1", "node_type": "assembly"}],
            "edges": [{"source_id": "n1", "target_id": "n1", "relation": "self"}],
            "metadata": {"ingested_by": self.descriptor.provider_id},
            "provenance": {"provider_id": self.descriptor.provider_id, "provider_family": "domain_graph"},
            "warnings": [],
        }
        return DomainGraphIngestResult(artifact=artifact, provenance=artifact["provenance"], warnings=[])


def test_domain_graph_artifact_schema_accepts_provider_neutral_payload() -> None:
    provider = _MockDomainGraphProvider()
    result = provider.ingest(DomainGraphIngestRequest(source_ref="project://demo", source_kind="cad_export"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(result.artifact))
    assert errors == []


def test_domain_graph_interface_module_has_no_tool_specific_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("blender", "kicad", "freecad"):
        assert forbidden not in source
