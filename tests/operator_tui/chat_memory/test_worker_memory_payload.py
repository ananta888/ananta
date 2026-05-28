from __future__ import annotations

from client_surfaces.operator_tui.chat_memory import ChatMemoryContext, MemoryTurn
from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder


def _build_v2(question: str, turns: list[tuple[str, str]], summary: str = "", pass_memory: bool = True) -> dict:
    mem = ChatMemoryContext(
        recent_turns=[MemoryTurn(role=r, content=c) for r, c in turns],
        rolling_summary=summary,
    )
    result = ChatPromptBuilder(question=question, depth="overview", memory=mem).build()
    payload = dict(result.worker_v2_payload)
    if not pass_memory:
        payload.pop("memory_context", None)
    return payload


def test_v2_payload_contains_recent_turns():
    payload = _build_v2("Test?", [("user", "Frage 1"), ("assistant", "Antwort 1")])
    turns = payload["memory_context"]["recent_turns"]
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Frage 1"


def test_v2_payload_contains_rolling_summary():
    payload = _build_v2("Frage?", [], summary="Bisherige Diskussion.")
    assert payload["memory_context"]["rolling_summary"] == "Bisherige Diskussion."


def test_v2_payload_contains_question():
    payload = _build_v2("Was ist das?", [])
    assert payload["question"] == "Was ist das?"


def test_v2_payload_contains_depth():
    payload = _build_v2("X?", [])
    assert "depth" in payload


def test_v2_payload_has_metadata_version():
    payload = _build_v2("X?", [])
    meta = payload["memory_context"]["metadata"]
    assert meta["memory_version"] == "v2"


def test_pass_memory_false_removes_memory_context():
    payload = _build_v2("X?", [("user", "hi")], pass_memory=False)
    assert "memory_context" not in payload


def test_v1_fallback_structure():
    """v1 fallback: only question, context, depth — no memory_context."""
    v1 = {"question": "Was?", "context": "some context", "depth": "overview"}
    assert "memory_context" not in v1
    assert "question" in v1 and "context" in v1


def test_v2_empty_turns_still_valid():
    payload = _build_v2("X?", [])
    assert payload["memory_context"]["recent_turns"] == []


def test_v2_codecompass_refs_present():
    mem = ChatMemoryContext(
        recent_turns=[],
        codecompass_refs=["ref1", "ref2"],
    )
    result = ChatPromptBuilder(question="X?", depth="overview", memory=mem).build()
    refs = result.worker_v2_payload["memory_context"].get("codecompass_refs", [])
    assert "ref1" in refs


def test_large_turn_list_trimmed_in_payload():
    turns = [("user", f"msg {i}") for i in range(50)]
    payload = _build_v2("X?", turns)
    assert len(payload["memory_context"]["recent_turns"]) <= 50
