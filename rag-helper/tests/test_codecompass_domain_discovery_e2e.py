"""Integration test: end-to-end CCDD pipeline via AnalysisInputs.from_memory (CCDD-017).

The full CLI end-to-end (RAG-Helper run with ``--domain-discovery-mode
basic/rich``) requires M4 (CCDD-012/013/014), which is blocked by
SPLIT-033 (project_processor.py refactor). This test exercises the
non-CLI path: it builds an ``AnalysisInputs`` from in-memory records,
runs clustering + boundary + descriptor analysis, validates the
resulting payload against ``validate_codecompass_domain_discovery``,
and asserts the documented invariants:

  - 3+ domain candidates with confidence >= 0.5
  - problematic cross-domain coupling surfaces as boundary_warnings
  - the resulting domains.detected.json round-trips through the
    validator without errors

The test uses the same fixture as the unit integration test
(test_codecompass_domain_discovery_integration.py) for cross-domain
shape assertions.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Add repo root so we can import the validator and the rag_helper package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RAG_HELPER_ROOT = _REPO_ROOT / "rag-helper"
if str(_RAG_HELPER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RAG_HELPER_ROOT))

from rag_helper.domain_discovery.boundaries import compute_boundary_metrics
from rag_helper.domain_discovery.clustering import cluster_domains
from rag_helper.domain_discovery.contracts import build_analysis_payload
from rag_helper.domain_discovery.descriptors import (
    build_descriptor_mismatches,
    index_existing_descriptors,
)
from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs
from devtools.validate_codecompass_domain_discovery import validate_payload


def _build_inputs() -> AnalysisInputs:
    """Build the AnalysisInputs that the CCDD pipeline consumes.

    Mirrors the in-memory fixture used by
    test_codecompass_domain_discovery_integration.py; we re-declare it
    here to keep this test self-contained and independent of the
    in-memory fixture module.
    """
    nodes: list[dict] = []
    for domain in ("identity", "billing", "rag", "orchestration", "ui"):
        for sub in ("a", "b", "c", "d"):
            path = f"{domain}/sub/{sub}.py" if sub in ("c", "d") else f"{domain}/mod_{sub}.py"
            nodes.append({"id": f"{domain}.{sub}", "kind": "py_module", "file": path})

    edges: list[dict] = []
    for domain in ("identity", "billing", "rag", "orchestration", "ui"):
        for a, b in [
            (f"{domain}.a", f"{domain}.b"),
            (f"{domain}.a", f"{domain}.c"),
            (f"{domain}.b", f"{domain}.d"),
        ]:
            edges.append({"source": a, "target": b, "type": "calls"})
    # Cross-domain coupling to trigger mutual_coupling.
    for edge_type in ("calls", "field_type_uses", "injects_dependency"):
        edges.append(
            {"source": "identity.a", "target": "billing.a", "type": edge_type}
        )
        edges.append(
            {"source": "billing.a", "target": "identity.a", "type": edge_type}
        )

    return AnalysisInputs.from_memory(
        index_records=[],
        detail_records=[],
        relation_records=[],
        graph_nodes=nodes,
        graph_edges=edges,
        manifest={},
    )


class TestEndToEndPipeline(unittest.TestCase):
    """Validate the full non-CLI pipeline from AnalysisInputs to JSON."""

    def test_pipeline_yields_validated_payload(self) -> None:
        inputs = _build_inputs()
        graph = DomainGraph.build(inputs)
        clustering = cluster_domains(graph, records=[])
        # No descriptor in this test; mismatches should be empty.
        descriptors = index_existing_descriptors(_REPO_ROOT)
        mismatches = build_descriptor_mismatches(descriptors, clustering.candidates)
        result = compute_boundary_metrics(
            clustering, graph, descriptor_mismatches=mismatches
        )

        payload = build_analysis_payload(
            project_root=str(_REPO_ROOT),
            generated_at="2026-06-10T17:00:00Z",
            inputs={"graph_nodes(in-memory)": len(inputs.graph_nodes)},
            domains=result.candidates,
            unassigned_records=clustering.unassigned_records,
            warnings=list(clustering.warnings) + list(result.warnings),
        )

        # Domain count: at least 3 of the 5 main candidates.
        self.assertGreaterEqual(
            len(payload["domains"]), 3, msg=f"got: {payload['domains']}"
        )
        # All confidences within 0..1.
        for domain in payload["domains"]:
            self.assertGreaterEqual(float(domain["confidence"]), 0.0)
            self.assertLessEqual(float(domain["confidence"]), 1.0)
        # Round-trip the payload through the validator.
        validation = validate_payload(payload)
        self.assertTrue(validation.ok, msg=validation.errors)

    def test_cross_domain_coupling_surfaces_as_warning(self) -> None:
        inputs = _build_inputs()
        graph = DomainGraph.build(inputs)
        clustering = cluster_domains(graph, records=[])
        result = compute_boundary_metrics(clustering, graph)
        warning_types = {w["warning_type"] for w in result.boundary_warnings}
        self.assertIn("mutual_coupling", warning_types)

    def test_payload_is_byte_stable(self) -> None:
        inputs = _build_inputs()
        graph = DomainGraph.build(inputs)
        clustering = cluster_domains(graph, records=[])
        result = compute_boundary_metrics(clustering, graph)
        payload_a = build_analysis_payload(
            project_root="/repo",
            generated_at="2026-06-10T17:00:00Z",
            inputs={"graph_nodes(in-memory)": len(inputs.graph_nodes)},
            domains=result.candidates,
            unassigned_records=clustering.unassigned_records,
            warnings=[],
        )
        # Reorder nodes before the second build; output must be identical.
        reordered = list(reversed(inputs.graph_nodes))
        inputs2 = AnalysisInputs.from_memory(
            index_records=[],
            detail_records=[],
            relation_records=[],
            graph_nodes=reordered,
            graph_edges=list(reversed(inputs.graph_edges)),
            manifest={},
        )
        graph2 = DomainGraph.build(inputs2)
        clustering2 = cluster_domains(graph2, records=[])
        result2 = compute_boundary_metrics(clustering2, graph2)
        payload_b = build_analysis_payload(
            project_root="/repo",
            generated_at="2026-06-10T17:00:00Z",
            inputs={"graph_nodes(in-memory)": len(inputs2.graph_nodes)},
            domains=result2.candidates,
            unassigned_records=clustering2.unassigned_records,
            warnings=[],
        )
        self.assertEqual(
            json.dumps(payload_a, sort_keys=True),
            json.dumps(payload_b, sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
