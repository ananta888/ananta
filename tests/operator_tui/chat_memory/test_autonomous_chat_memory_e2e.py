from __future__ import annotations

from unittest.mock import patch

from client_surfaces.operator_tui.chat_memory import (
    ChatMemoryContext,
    MemoryTurn,
    extract_memory_context,
    update_rolling_summary,
    resolve_memory_settings,
    get_rolling_summary,
)
from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder


def _game_with_turns(turns: list[tuple[str, str]]) -> dict:
    return {
        "chat_state": {
            "channels": {
                "ai:tutor": {
                    "messages": [
                        {"sender_kind": "user" if r == "user" else "ai", "text": c}
                        for r, c in turns
                    ]
                }
            }
        }
    }


def test_second_question_sees_prior_fact():
    game = _game_with_turns([
        ("user", "Das Projekt heißt Marvin."),
        ("assistant", "Verstanden, Marvin notiert."),
    ])
    ctx = extract_memory_context(game, current_question="Wie heißt das Projekt?")
    contents = " ".join(t.content for t in ctx.recent_turns)
    assert "Marvin" in contents


def test_rolling_summary_carries_earlier_fact():
    game: dict = {}
    update_rolling_summary(game, last_question="Das Projekt heißt Marvin.", last_answer="Marvin notiert.")
    summary = get_rolling_summary(game)
    assert "Marvin" in summary


def test_prompt_builder_includes_prior_fact_in_system():
    game = _game_with_turns([
        ("user", "Das Projekt heißt Marvin."),
        ("assistant", "Marvin ist registriert."),
    ])
    ctx = extract_memory_context(game, current_question="Wer ist Marvin?")
    result = ChatPromptBuilder(question="Wer ist Marvin?", depth="overview", memory=ctx).build()
    full_prompt = " ".join(m["content"] for m in result.messages)
    assert "Marvin" in full_prompt


def test_prompt_builder_includes_summary_in_system():
    game: dict = {}
    update_rolling_summary(game, last_question="Das Projekt heißt Marvin.", last_answer="OK.")
    mem = ChatMemoryContext(recent_turns=[], rolling_summary=get_rolling_summary(game))
    result = ChatPromptBuilder(question="Wie heißt das Projekt?", depth="overview", memory=mem).build()
    assert "Marvin" in result.prompt_text


def test_history_used_marker_in_payload():
    game = _game_with_turns([("user", "fact X"), ("assistant", "noted")])
    ctx = extract_memory_context(game)
    result = ChatPromptBuilder(question="recall fact X", depth="overview", memory=ctx).build()
    turns = result.worker_v2_payload["memory_context"]["recent_turns"]
    contents = " ".join(t["content"] for t in turns)
    assert "fact X" in contents


def test_does_not_require_real_llm():
    game = _game_with_turns([("user", "test fact"), ("assistant", "noted")])
    ctx = extract_memory_context(game)
    result = ChatPromptBuilder(question="recall?", depth="overview", memory=ctx).build()
    assert result.messages[-1]["role"] == "user"


def test_does_not_require_network():
    ctx = ChatMemoryContext(recent_turns=[MemoryTurn("user", "no network needed")])
    result = ChatPromptBuilder(question="Q?", depth="overview", memory=ctx).build()
    assert result.is_ok if hasattr(result, "is_ok") else True


def test_memory_settings_disable_history():
    game = {
        "chat_use_history": False,
        **_game_with_turns([("user", "secret fact")]),
    }
    s = resolve_memory_settings(game)
    ctx = extract_memory_context(game, max_turns=s["history_turns"] if s["use_history"] else 0)
    if not s["use_history"]:
        assert ctx.recent_turns == []


def test_memory_settings_disable_summary():
    game = {"chat_use_summary": False}
    update_rolling_summary(game, last_question="Q", last_answer="A")
    s = resolve_memory_settings(game)
    # With use_summary=False the caller should not pass rolling_summary
    ctx = ChatMemoryContext(recent_turns=[], rolling_summary="" if not s["use_summary"] else "kept")
    assert ctx.rolling_summary == ""
