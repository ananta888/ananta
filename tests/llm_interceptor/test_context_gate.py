from __future__ import annotations

from agent.services.llm_interceptor.context_gate import ContextGate


def test_context_gate_local_allows_repo_and_rag():
    gate = ContextGate({})
    snippets = [
        {"source_type": "repo", "content": "code", "sensitivity": "internal"},
        {"source_type": "rag", "content": "doc", "sensitivity": "public"},
    ]
    allowed, meta = gate.gate(snippets=snippets, upstream_trust_level="local", decision={"action": "allow"})
    assert len(allowed) == 2
    assert meta["denied_count"] == 0


def test_context_gate_cloud_blocks_repo_and_high_sensitivity():
    gate = ContextGate({})
    snippets = [
        {"source_type": "repo", "content": "code", "sensitivity": "internal"},
        {"source_type": "rag", "content": "s", "sensitivity": "secret"},
        {"source_type": "rag", "content": "ok", "sensitivity": "public"},
    ]
    allowed, meta = gate.gate(snippets=snippets, upstream_trust_level="cloud", decision={"action": "reduce_context"})
    assert len(allowed) == 1
    assert allowed[0]["content"] == "ok"
    assert meta["denied_count"] == 2

