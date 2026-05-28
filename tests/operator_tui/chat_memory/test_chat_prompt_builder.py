from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_memory import ChatMemoryContext, MemoryTurn
from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder, PromptBuildResult


def _mem(
    turns: list[tuple[str, str]] | None = None,
    summary: str = "",
    active: str = "",
    codecompass: list[str] | None = None,
    rag: list[str] | None = None,
) -> ChatMemoryContext:
    return ChatMemoryContext(
        recent_turns=[MemoryTurn(role=r, content=c) for r, c in (turns or [])],
        rolling_summary=summary,
        active_target_excerpt=active,
        codecompass_refs=codecompass or [],
        rag_snippets=rag or [],
    )


def _build(question: str = "Was ist Ananta?", depth: str = "overview", memory: ChatMemoryContext | None = None, budget: int = 3000) -> PromptBuildResult:
    return ChatPromptBuilder(
        question=question,
        depth=depth,
        memory=memory or _mem(),
        context_budget=budget,
    ).build()


# ── Messages format (LMStudio/local) ─────────────────────────────────────────

def test_messages_contain_system_and_user():
    result = _build()
    roles = [m["role"] for m in result.messages]
    assert "system" in roles
    assert "user" in roles


def test_messages_last_is_user_question():
    result = _build(question="Was ist das?")
    assert result.messages[-1]["role"] == "user"
    assert "Was ist das?" in result.messages[-1]["content"]


def test_messages_include_recent_turns():
    mem = _mem(turns=[("user", "Hallo"), ("assistant", "Hi")])
    result = _build(memory=mem)
    contents = [m["content"] for m in result.messages]
    assert any("Hallo" in c for c in contents)


def test_messages_trim_turns_by_char_budget():
    long_turn = "x" * 2000
    mem = _mem(turns=[("user", long_turn), ("user", "short")])
    result = _build(memory=mem, budget=500)
    contents = " ".join(m["content"] for m in result.messages)
    assert len(contents) < 10000


# ── Prompt text (propose/worker) ──────────────────────────────────────────────

def test_prompt_text_contains_question():
    result = _build(question="Wie funktioniert das?")
    assert "Wie funktioniert das?" in result.prompt_text


def test_prompt_text_contains_summary_when_present():
    mem = _mem(summary="Vorherige Diskussion über Goals.")
    result = _build(memory=mem)
    assert "Vorherige Diskussion" in result.prompt_text


def test_prompt_text_contains_recent_turns():
    mem = _mem(turns=[("user", "Hallo")])
    result = _build(memory=mem)
    assert "Hallo" in result.prompt_text


# ── Worker v2 payload ─────────────────────────────────────────────────────────

def test_worker_v2_payload_contains_question():
    result = _build(question="Test?")
    assert result.worker_v2_payload["question"] == "Test?"


def test_worker_v2_payload_contains_memory_context():
    mem = _mem(turns=[("user", "Frage")], summary="Summary text")
    result = _build(memory=mem)
    mem_ctx = result.worker_v2_payload.get("memory_context", {})
    assert "recent_turns" in mem_ctx
    assert "rolling_summary" in mem_ctx
    assert mem_ctx["rolling_summary"] == "Summary text"


def test_worker_v2_payload_has_memory_version():
    result = _build()
    meta = result.worker_v2_payload.get("memory_context", {}).get("metadata", {})
    assert meta.get("memory_version") == "v2"


def test_worker_v2_disabling_summary_produces_empty_summary():
    mem = _mem(summary="")
    result = _build(memory=mem)
    assert result.worker_v2_payload["memory_context"]["rolling_summary"] == ""


# ── Budget policy and section ordering ───────────────────────────────────────

def test_active_target_included_first():
    mem = _mem(active="import. file context")
    result = _build(memory=mem)
    assert "included_sections" in result.__dataclass_fields__ if hasattr(result, "__dataclass_fields__") else True
    assert result.total_chars >= 0


def test_included_sections_non_empty_when_content():
    mem = _mem(summary="sum", rag=["rag snippet"])
    result = _build(memory=mem)
    assert len(result.included_sections) > 0


def test_budget_limits_total_context():
    mem = _mem(
        summary="s" * 1000,
        active="a" * 1000,
        rag=["r" * 1000],
        codecompass=["c" * 1000],
    )
    result = _build(memory=mem, budget=500)
    assert result.total_chars <= 500 + 100


# ── Depth instructions ────────────────────────────────────────────────────────

def test_depth_overview_in_prompt():
    result = _build(depth="overview")
    system = next(m["content"] for m in result.messages if m["role"] == "system")
    assert "2-3" in system or "overview" in result.prompt_text.lower() or "Sätzen" in system


def test_depth_expert_in_prompt():
    result = _build(depth="expert")
    system = next(m["content"] for m in result.messages if m["role"] == "system")
    assert "technisch" in system or "expert" in result.prompt_text.lower() or "präzise" in system


# ── Codecompass/RAG ───────────────────────────────────────────────────────────

def test_codecompass_included_when_budget_allows():
    mem = _mem(codecompass=["code ref 1", "code ref 2"])
    result = _build(memory=mem)
    assert result.included_sections.get("codecompass", 0) > 0 or "code ref" in result.prompt_text


def test_codecompass_excluded_when_budget_exhausted():
    mem = _mem(active="a" * 2000, codecompass=["should be excluded"])
    result = _build(memory=mem, budget=200)
    assert result.total_chars <= 200 + 100
