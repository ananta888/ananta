from __future__ import annotations

import ast
from pathlib import Path

from ananta_codecompass.architecture_query import run_architecture_query
from ananta_codecompass.candidate_resolver import CodeCompassCandidateResolver
from ananta_codecompass.embedding_loader import load_codecompass_embedding_documents
from ananta_codecompass.graph_expansion import expand_codecompass_graph
from ananta_codecompass.graph_store import CodeCompassGraphStore
from ananta_codecompass.output_reader import CodeCompassOutputReader
from ananta_codecompass.query_parser import parse_codecompass_query
from ananta_codecompass.repository_intelligence_query import run_query
from ananta_codecompass.vector_engine import CodeCompassVectorEngine
from worker.retrieval import codecompass_architecture_query as worker_query
from worker.retrieval import codecompass_candidate_resolver as worker_candidates
from worker.retrieval import codecompass_embedding_loader as worker_embeddings
from worker.retrieval import codecompass_graph_expansion as worker_expansion
from worker.retrieval import codecompass_graph_store as worker_store
from worker.retrieval import codecompass_output_reader as worker_output
from worker.retrieval import codecompass_query_parser as worker_parser
from worker.retrieval import codecompass_repository_intelligence_query as worker_repository_query
from worker.retrieval import codecompass_vector_engine as worker_vector


def test_worker_graph_modules_are_compatibility_facades() -> None:
    assert worker_store.CodeCompassGraphStore is CodeCompassGraphStore
    assert worker_expansion.expand_codecompass_graph is expand_codecompass_graph
    assert worker_query.run_architecture_query is run_architecture_query
    assert worker_candidates.CodeCompassCandidateResolver is CodeCompassCandidateResolver
    assert (
        worker_embeddings.load_codecompass_embedding_documents
        is load_codecompass_embedding_documents
    )
    assert worker_output.CodeCompassOutputReader is CodeCompassOutputReader
    assert worker_parser.parse_codecompass_query is parse_codecompass_query
    assert worker_repository_query.run_query is run_query
    assert worker_vector.CodeCompassVectorEngine is CodeCompassVectorEngine


def test_agent_production_code_does_not_import_worker_graph_implementation() -> None:
    root = Path(__file__).parents[1]
    violations: list[str] = []
    for path in (root / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and str(node.module or "").startswith(
                    "worker.retrieval.codecompass_"
                )
            ):
                violations.append(str(path.relative_to(root)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(
                        "worker.retrieval.codecompass_"
                    ):
                        violations.append(str(path.relative_to(root)))

    assert violations == []
