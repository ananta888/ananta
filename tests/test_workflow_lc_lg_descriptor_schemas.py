"""Tests for the chain and graph descriptor JSON schemas (LCG-014, LCG-015).

These schemas are validated without LangChain/LangGraph installed —
they are the contract; the framework is an implementation detail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# jsonschema is already a transitive dependency of pydantic in this
# codebase, but we use it directly here to make the intent explicit.
from jsonschema import Draft202012Validator, ValidationError


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts"
CHAIN_SCHEMA = SCHEMA_DIR / "langchain-chain-descriptor.schema.json"
GRAPH_SCHEMA = SCHEMA_DIR / "langgraph-graph-descriptor.schema.json"


@pytest.fixture(scope="module")
def chain_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CHAIN_SCHEMA.read_text()))


@pytest.fixture(scope="module")
def graph_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(GRAPH_SCHEMA.read_text()))


# ── Schema files are valid Draft 2020-12 ───────────────────────────────


def test_chain_schema_is_valid_draft202012():
    Draft202012Validator.check_schema(json.loads(CHAIN_SCHEMA.read_text()))


def test_graph_schema_is_valid_draft202012():
    Draft202012Validator.check_schema(json.loads(GRAPH_SCHEMA.read_text()))


# ── Minimal valid examples ─────────────────────────────────────────────


_MIN_CHAIN = {
    "schema": "langchain-chain-descriptor.v1",
    "id": "chain.test.rag_query.v1",
    "purpose": "Smoke test chain",
    "inputs": [{"name": "query", "type": "str"}],
    "outputs": [{"name": "answer", "type": "str"}],
    "model_ref": "local.default",
    "policies": {"budget": {"max_steps": 5}},
}


_MIN_GRAPH = {
    "schema": "langgraph-graph-descriptor.v1",
    "graph_id": "graph.test.smoke.v1",
    "nodes": [
        {"id": "n1", "kind": "llm"},
        {"id": "n2", "kind": "end"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
    "entrypoint": "n1",
    "stop_conditions": {"max_iterations": 5},
}


def test_minimal_chain_validates(chain_validator):
    chain_validator.validate(_MIN_CHAIN)


def test_minimal_graph_validates(graph_validator):
    graph_validator.validate(_MIN_GRAPH)


# ── Schema field is required and must be the documented value ─────────


def test_chain_with_wrong_schema_rejected(chain_validator):
    bad = dict(_MIN_CHAIN, schema="langchain-chain-descriptor.v2")
    with pytest.raises(ValidationError):
        chain_validator.validate(bad)


def test_graph_with_wrong_schema_rejected(graph_validator):
    bad = dict(_MIN_GRAPH, schema="langgraph-graph-descriptor.v2")
    with pytest.raises(ValidationError):
        graph_validator.validate(bad)


# ── Retriever constraint: codecompass or null only ────────────────────


def test_chain_retriever_must_be_codecompass_or_null(chain_validator):
    bad = dict(_MIN_CHAIN, retriever={"source": "pinecone"})
    with pytest.raises(ValidationError) as exc:
        chain_validator.validate(bad)
    assert "retriever" in str(exc.value).lower() or "additional" in str(exc.value).lower()


# ── Graph node kinds enum ─────────────────────────────────────────────


@pytest.mark.parametrize("kind", [
    "llm", "tool", "human_gate", "router", "artifact_writer",
    "retriever", "end",
])
def test_graph_accepts_all_documented_node_kinds(graph_validator, kind):
    g = {
        "schema": "langgraph-graph-descriptor.v1",
        "graph_id": "graph.test.kinds.v1",
        "nodes": [{"id": "n1", "kind": kind}, {"id": "n2", "kind": "end"}],
        "edges": [{"from": "n1", "to": "n2"}],
        "entrypoint": "n1",
        "stop_conditions": {"max_iterations": 5},
    }
    graph_validator.validate(g)


def test_graph_rejects_unknown_node_kind(graph_validator):
    g = {
        "schema": "langgraph-graph-descriptor.v1",
        "graph_id": "graph.test.bad_kind.v1",
        "nodes": [{"id": "n1", "kind": "llm"}, {"id": "n2", "kind": "magic"}],
        "edges": [{"from": "n1", "to": "n2"}],
        "entrypoint": "n1",
        "stop_conditions": {"max_iterations": 5},
    }
    with pytest.raises(ValidationError) as exc:
        graph_validator.validate(g)
    assert "kind" in str(exc.value) or "magic" in str(exc.value)


# ── unknown top-level properties rejected (additionalProperties: false) ──


def test_chain_rejects_unknown_top_level_property(chain_validator):
    bad = dict(_MIN_CHAIN, mystery_field="value")
    with pytest.raises(ValidationError):
        chain_validator.validate(bad)


def test_graph_rejects_unknown_top_level_property(graph_validator):
    bad = dict(_MIN_GRAPH, mystery_field="value")
    with pytest.raises(ValidationError):
        graph_validator.validate(bad)
