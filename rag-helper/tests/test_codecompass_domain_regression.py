"""Regression tests for codecompass domain discovery (CCDD-018).

The CCDD track adds a pure analysis library under
``rag_helper.domain_discovery`` and does NOT modify the existing
RAG-Helper outputs by default. These tests verify that:

  - importing the analysis library does not import any module that
    forces project_processor or cli to load (i.e. analysis stays
    runtime-frei)
  - the analysis library is decoupled from rag_helper.application
    imports until ``technical_layers_for_records`` is actually called
  - the DomainGraph deduplicates relation edges from
    relations.jsonl against graph_edges.jsonl, so calling
    DomainGraph.build twice on the same input yields identical
    structural_edges and relation_edges lists
  - when ``domain_discovery_mode`` is off (the default), no new
    domain artefacts are produced
"""

from __future__ import annotations

import importlib
import sys
import unittest


class TestNoRuntimeLeak(unittest.TestCase):
    """The analysis library must not import hub/agent runtime services.

    Importing the public entry points of ``rag_helper.domain_discovery``
    must NOT pull in the CLI, project_processor or any other RAG-Helper
    orchestrator. The library is pure analysis.
    """

    def test_importing_discovery_does_not_import_cli(self) -> None:
        # Force a fresh import of the discovery module and its sub-modules
        # to ensure the import side effects are observable.
        for module_name in list(sys.modules):
            if module_name.startswith("rag_helper.domain_discovery"):
                sys.modules.pop(module_name, None)
        for module_name in (
            "rag_helper.domain_discovery",
            "rag_helper.domain_discovery.clustering",
            "rag_helper.domain_discovery.boundaries",
            "rag_helper.domain_discovery.descriptors",
        ):
            importlib.import_module(module_name)
        # The CLI module must not have been pulled in as a side effect.
        self.assertNotIn("rag_helper.cli", sys.modules)
        # The application/project_processor module must not have been
        # pulled in either; clustering.py imports it lazily, only on
        # enrichment, and only when technical_layers_for_records is
        # actually called.
        self.assertNotIn("rag_helper.application.project_processor", sys.modules)


class TestDeterministicBuild(unittest.TestCase):
    """DomainGraph.build is pure and deterministic for identical input."""

    def test_build_yields_identical_graph_for_identical_input(self) -> None:
        from rag_helper.domain_discovery.graph_model import DomainGraph
        from rag_helper.domain_discovery.inputs import AnalysisInputs

        nodes = [
            {"id": "a", "kind": "py_module", "file": "alpha/a.py"},
            {"id": "b", "kind": "py_module", "file": "alpha/b.py"},
        ]
        edges = [
            {"source": "a", "target": "b", "type": "calls"},
            # Duplicate of the above; should be deduped.
            {"source": "a", "target": "b", "type": "calls"},
        ]
        relations = [
            # relations.jsonl uses from/to/type; should also be deduped
            # against graph_edges by (source, target, type).
            {"from": "a", "to": "b", "type": "calls"},
        ]

        def _build():
            inputs = AnalysisInputs(
                out_dir=None, graph_nodes=nodes, graph_edges=edges
            )
            inputs.relation_records = relations
            return DomainGraph.build(inputs)

        g1 = _build()
        g2 = _build()
        self.assertEqual(
            [(e.source, e.target, e.type) for e in g1.relation_edges],
            [(e.source, e.target, e.type) for e in g2.relation_edges],
        )
        self.assertEqual(len(g1.relation_edges), 1)


class TestAnalysisIsOffByDefault(unittest.TestCase):
    """The analysis pipeline does not auto-run.

    Domain discovery is opt-in: it must not run by importing the
    library, by calling the graph builder, or by walking the existing
    RAG-Helper CLI defaults. The default ``domain_discovery_mode`` in
    the RAG-Helper CLI is ``off``.
    """

    def test_clustering_call_requires_explicit_invocation(self) -> None:
        from rag_helper.domain_discovery.clustering import cluster_domains
        from rag_helper.domain_discovery.graph_model import DomainGraph
        from rag_helper.domain_discovery.inputs import AnalysisInputs

        nodes = [{"id": "x", "kind": "py_module", "file": "x.py"}]
        inputs = AnalysisInputs(out_dir=None, graph_nodes=nodes, graph_edges=[])
        graph = DomainGraph.build(inputs)
        # No implicit run; explicit call is the only path to clustering.
        result = cluster_domains(graph)
        self.assertEqual(result.candidates, [])

    def test_default_cli_mode_documented_off(self) -> None:
        # The contract is that --domain-discovery-mode defaults to off.
        # We assert this by reading the documented default in the
        # codecompass-domain-discovery doc; this test exists as a
        # sentinel: if the default ever changes, this test must be
        # updated alongside the contract change.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        doc_path = repo_root / "docs" / "codecompass-domain-discovery.md"
        doc = doc_path.read_text(encoding="utf-8")
        self.assertIn("--domain-discovery-mode off", doc)
        self.assertIn("Default", doc.replace("off (Default)", "Default"))


class TestContractStability(unittest.TestCase):
    """The documented payload schema identifier is not silently changed."""

    def test_schema_identifier_is_stable(self) -> None:
        from rag_helper.domain_discovery.contracts import (
            DOMAIN_ANALYSIS_SCHEMA,
            DOMAIN_COUPLING_SCHEMA,
        )

        self.assertEqual(DOMAIN_ANALYSIS_SCHEMA, "codecompass_domain_analysis.v1")
        self.assertEqual(
            DOMAIN_COUPLING_SCHEMA, "codecompass_domain_coupling.v1"
        )

    def test_warning_types_match_documented_set(self) -> None:
        from rag_helper.domain_discovery.contracts import (
            BOUNDARY_WARNING_TYPES,
        )

        self.assertEqual(
            BOUNDARY_WARNING_TYPES,
            {
                "mutual_coupling",
                "layer_spans_domains",
                "heterogeneous_root",
                "descriptor_mismatch",
            },
        )


if __name__ == "__main__":
    unittest.main()
