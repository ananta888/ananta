"""Regression guard: every chain/graph example in examples/ must validate.

If you add a new descriptor under examples/langchain or
examples/langgraph, this test will pick it up automatically. It
prevents shipping a broken example that the user can never load.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAIN_SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "contracts" / "langchain-chain-descriptor.schema.json")
    .read_text()
)
GRAPH_SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "contracts" / "langgraph-graph-descriptor.schema.json")
    .read_text()
)


def _chain_examples() -> list[Path]:
    return sorted((REPO_ROOT / "examples" / "langchain").glob("*.json"))


def _graph_examples() -> list[Path]:
    return sorted((REPO_ROOT / "examples" / "langgraph").glob("*.json"))


@pytest.mark.parametrize("path", _chain_examples(), ids=lambda p: p.name)
def test_chain_example_validates(path: Path):
    data = json.loads(path.read_text())
    Draft202012Validator(CHAIN_SCHEMA).validate(data)
    # Cross-check: id field follows the convention 'chain.example.<name>.v<N>'.
    assert data["id"].startswith("chain.example."), \
        f"Chain example id should follow chain.example.* convention, got {data['id']!r}"


@pytest.mark.parametrize("path", _graph_examples(), ids=lambda p: p.name)
def test_graph_example_validates(path: Path):
    data = json.loads(path.read_text())
    Draft202012Validator(GRAPH_SCHEMA).validate(data)
    assert data["graph_id"].startswith("graph.example."), \
        f"Graph example id should follow graph.example.* convention, got {data['graph_id']!r}"


def test_examples_directory_is_not_empty():
    """If somebody accidentally removes the directory contents, this
    catches it before we ship a docs/setup page that points nowhere."""
    assert _chain_examples(), "examples/langchain/ is empty"
    assert _graph_examples(), "examples/langgraph/ is empty"
