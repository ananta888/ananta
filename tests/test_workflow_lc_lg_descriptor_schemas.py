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
CHAIN_SCHEMA_V11 = SCHEMA_DIR / "langchain-chain-descriptor.v1.1.json"
GRAPH_SCHEMA_V11 = SCHEMA_DIR / "langgraph-graph-descriptor.v1.1.json"


@pytest.fixture(scope="module")
def chain_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CHAIN_SCHEMA.read_text()))


@pytest.fixture(scope="module")
def graph_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(GRAPH_SCHEMA.read_text()))


@pytest.fixture(scope="module")
def chain_v11_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CHAIN_SCHEMA_V11.read_text()))


@pytest.fixture(scope="module")
def graph_v11_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(GRAPH_SCHEMA_V11.read_text()))


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


# ── v1.1 schemas (LCG-066) ────────────────────────────────────────────────────


def test_chain_v11_schema_is_valid_draft202012():
    """v1.1 chain schema is itself valid JSON Schema 2020-12."""
    Draft202012Validator.check_schema(json.loads(CHAIN_SCHEMA_V11.read_text()))


def test_graph_v11_schema_is_valid_draft202012():
    """v1.1 graph schema is itself valid JSON Schema 2020-12."""
    Draft202012Validator.check_schema(json.loads(GRAPH_SCHEMA_V11.read_text()))


def test_v10_chain_validates_against_v11(chain_v11_validator):
    """All v1.0 chain descriptors validate against the v1.1 schema (additive only)."""
    chain_v11_validator.validate(_MIN_CHAIN)


def test_v10_graph_validates_against_v11(graph_v11_validator):
    """All v1.0 graph descriptors validate against the v1.1 schema (additive only)."""
    graph_v11_validator.validate(_MIN_GRAPH)


def test_chain_v11_allows_v11_schema_marker(chain_v11_validator):
    """v1.1 schema accepts 'langchain-chain-descriptor.v1.1' as schema marker."""
    chain_v11 = dict(_MIN_CHAIN, schema="langchain-chain-descriptor.v1.1")
    chain_v11_validator.validate(chain_v11)


def test_graph_v11_allows_v11_schema_marker(graph_v11_validator):
    """v1.1 schema accepts 'langgraph-graph-descriptor.v1.1' as schema marker."""
    graph_v11 = dict(_MIN_GRAPH, schema="langgraph-graph-descriptor.v1.1")
    graph_v11_validator.validate(graph_v11)


def test_chain_v11_prompt_template_field(chain_v11_validator):
    """v1.1: prompt_template field is accepted."""
    chain = dict(_MIN_CHAIN, prompt_template="Answer: {query}", output_format="text")
    chain_v11_validator.validate(chain)


def test_chain_v11_output_format_json(chain_v11_validator):
    """v1.1: output_format='json' is accepted."""
    chain = dict(_MIN_CHAIN, output_format="json")
    chain_v11_validator.validate(chain)


def test_chain_v11_model_provider_ref(chain_v11_validator):
    """v1.1: model_provider_ref field is accepted."""
    chain = dict(_MIN_CHAIN, model_provider_ref="ollama.llama3.1")
    chain_v11_validator.validate(chain)


def test_graph_v11_subgraph_node_kind(graph_v11_validator):
    """v1.1: 'subgraph' is a valid node kind."""
    graph = dict(
        _MIN_GRAPH,
        nodes=[
            {"id": "main", "kind": "llm"},
            {"id": "sub", "kind": "subgraph", "subgraph_ref": "analysis_subgraph"},
            {"id": "end", "kind": "end"},
        ],
        edges=[
            {"from": "main", "to": "sub"},
            {"from": "sub", "to": "end"},
        ],
    )
    graph_v11_validator.validate(graph)


def test_graph_v11_conditional_edge_object(graph_v11_validator):
    """v1.1: edge condition can be an object with on_stop_reason."""
    graph = dict(
        _MIN_GRAPH,
        nodes=[
            {"id": "router", "kind": "router"},
            {"id": "happy", "kind": "end"},
            {"id": "sad", "kind": "end"},
        ],
        edges=[
            {"from": "router", "to": "happy",
             "condition": {"on_stop_reason": "success"}},
            {"from": "router", "to": "sad"},
        ],
        entrypoint="router",
    )
    graph_v11_validator.validate(graph)


def test_graph_v11_conditional_edge_string(graph_v11_validator):
    """v1.1: edge condition can be a plain string (on_stop_reason shorthand)."""
    graph = dict(
        _MIN_GRAPH,
        nodes=[
            {"id": "router", "kind": "router"},
            {"id": "dest", "kind": "end"},
        ],
        edges=[
            {"from": "router", "to": "dest", "condition": "end_node"},
        ],
        entrypoint="router",
    )
    graph_v11_validator.validate(graph)


def test_graph_v11_retriever_node_with_retriever_ref(graph_v11_validator):
    """v1.1: retriever node can declare retriever_ref='codecompass'."""
    graph = dict(
        _MIN_GRAPH,
        nodes=[
            {"id": "fetch", "kind": "retriever", "retriever_ref": "codecompass"},
            {"id": "end", "kind": "end"},
        ],
        edges=[{"from": "fetch", "to": "end"}],
        entrypoint="fetch",
    )
    graph_v11_validator.validate(graph)


def test_graph_v11_artifact_writer_node(graph_v11_validator):
    """v1.1: artifact_writer node can declare artifact_type."""
    graph = dict(
        _MIN_GRAPH,
        nodes=[
            {"id": "writer", "kind": "artifact_writer", "artifact_type": "report"},
            {"id": "end", "kind": "end"},
        ],
        edges=[{"from": "writer", "to": "end"}],
        entrypoint="writer",
    )
    graph_v11_validator.validate(graph)
