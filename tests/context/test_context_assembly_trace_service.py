"""Tests for ContextAssemblyTraceService — T04."""
from __future__ import annotations

import pytest
from agent.services.context_assembly_trace_service import (
    ContextAssemblyTrace,
    ContextAssemblyTraceService,
    ContextPart,
)


def _svc(**kwargs) -> ContextAssemblyTraceService:
    return ContextAssemblyTraceService(**kwargs)


# ── Initialization ────────────────────────────────────────────────────────────

def test_store_prompt_text_raises():
    with pytest.raises(ValueError, match="store_prompt_text"):
        ContextAssemblyTraceService(store_prompt_text=True)


def test_start_trace_creates_trace():
    svc = _svc()
    trace = svc.start_trace(decision_ref="decision-abc")
    assert isinstance(trace, ContextAssemblyTrace)
    assert trace.decision_ref == "decision-abc"
    assert trace.trace_ref  # non-empty UUID
    assert trace.parts == []


# ── add_part ──────────────────────────────────────────────────────────────────

def test_add_part_does_not_store_text():
    svc = _svc(store_prompt_hashes=False)
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(
        trace,
        source_type="user",
        source_ref="msg:1",
        text="This is a secret message",
        estimated_tokens=10,
        included=True,
    )
    assert len(trace.parts) == 1
    part = trace.parts[0]
    assert not hasattr(part, "text")
    assert part.content_hash is None  # hashes disabled


def test_add_part_stores_hash_when_enabled():
    svc = _svc(store_prompt_hashes=True)
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(
        trace,
        source_type="user",
        source_ref="msg:1",
        text="hello world",
        estimated_tokens=5,
        included=True,
    )
    part = trace.parts[0]
    assert part.content_hash is not None
    assert len(part.content_hash) == 16  # first 16 hex chars of sha256


def test_add_part_none_text_no_hash():
    svc = _svc(store_prompt_hashes=True)
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(
        trace,
        source_type="system",
        source_ref="sys:1",
        text=None,
        estimated_tokens=100,
        included=True,
    )
    part = trace.parts[0]
    assert part.content_hash is None


def test_add_excluded_part_with_blocked_reason():
    svc = _svc()
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(
        trace,
        source_type="rag_context",
        source_ref="rag:chunk42",
        text="some rag content",
        estimated_tokens=200,
        included=False,
        blocked_reason="safe_minimal_chat_blocks_rag",
    )
    part = trace.parts[0]
    assert part.included is False
    assert part.blocked_reason == "safe_minimal_chat_blocks_rag"


# ── finalize ──────────────────────────────────────────────────────────────────

def test_finalize_sums_included_tokens():
    svc = _svc()
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(trace, source_type="user", source_ref="u1", text=None, estimated_tokens=100, included=True)
    svc.add_part(trace, source_type="history", source_ref="h1", text=None, estimated_tokens=200, included=True)
    svc.add_part(trace, source_type="rag_context", source_ref="r1", text=None, estimated_tokens=500, included=False, blocked_reason="budget")
    svc.finalize(trace, reserved_output_tokens=1024)
    assert trace.estimated_input_tokens == 300  # only included parts
    assert trace.reserved_output_tokens == 1024


def test_finalize_truncated_parts():
    svc = _svc()
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(trace, source_type="user", source_ref="u1", text=None, estimated_tokens=50, included=True)
    svc.add_part(trace, source_type="rag_context", source_ref="r1", text=None, estimated_tokens=500, included=False, blocked_reason="limit")
    svc.add_part(trace, source_type="tool_schemas", source_ref="t1", text=None, estimated_tokens=300, included=False, blocked_reason="limit")
    svc.finalize(trace)
    assert "rag_context" in trace.truncated_parts
    assert "tool_schemas" in trace.truncated_parts
    assert "user" not in trace.truncated_parts


def test_as_dict_structure():
    svc = _svc()
    trace = svc.start_trace(decision_ref="d1")
    svc.add_part(trace, source_type="user", source_ref="u1", text="hi", estimated_tokens=5, included=True)
    svc.finalize(trace, reserved_output_tokens=512)
    d = trace.as_dict()
    assert "trace_ref" in d
    assert "decision_ref" in d
    assert "parts" in d
    assert "estimated_input_tokens" in d
    assert "reserved_output_tokens" in d
    assert "truncated_parts" in d
    assert d["parts"][0]["source_ref"] == "u1"
    assert "text" not in d["parts"][0]  # never in dict
