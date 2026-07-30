from __future__ import annotations

import ast
from pathlib import Path

from ananta_codecompass.architecture_query import run_architecture_query
from ananta_codecompass.graph_expansion import expand_codecompass_graph
from ananta_codecompass.graph_store import CodeCompassGraphStore
from worker.retrieval import codecompass_architecture_query as worker_query
from worker.retrieval import codecompass_graph_expansion as worker_expansion
from worker.retrieval import codecompass_graph_store as worker_store


def test_worker_graph_modules_are_compatibility_facades() -> None:
    assert worker_store.CodeCompassGraphStore is CodeCompassGraphStore
    assert worker_expansion.expand_codecompass_graph is expand_codecompass_graph
    assert worker_query.run_architecture_query is run_architecture_query


def test_agent_production_code_does_not_import_worker_graph_implementation() -> None:
    root = Path(__file__).parents[1]
    violations: list[str] = []
    forbidden_prefix = "worker.retrieval.codecompass_"
    for path in (root / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith(
                forbidden_prefix
            ):
                violations.append(str(path.relative_to(root)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefix):
                        violations.append(str(path.relative_to(root)))

    assert violations == []
