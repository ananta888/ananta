from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_memory import (
    ChatMemoryContext,
    MemoryTurn,
    extract_memory_context,
    get_rolling_summary,
    resolve_memory_settings,
    set_rolling_summary,
    update_rolling_summary,
)


def _game_with_messages(messages: list[dict]) -> dict:
    return {
        "chat_state": {
            "channels": {
                "ai:tutor": {
                    "messages": messages
                }
            }
        }
    }


def _msg(kind: str, text: str) -> dict:
    return {"sender_kind": kind, "text": text}


# ── Recent Turns ──────────────────────────────────────────────────────────────

def test_extract_empty_game():
    ctx = extract_memory_context({})
    assert ctx.recent_turns == []


def test_extract_basic_turns():
    game = _game_with_messages([
        _msg("user", "Was ist Ananta?"),
        _msg("ai", "Ananta ist ein autonomes System."),
    ])
    ctx = extract_memory_context(game)
    assert len(ctx.recent_turns) == 2
    assert ctx.recent_turns[0].role == "user"
    assert ctx.recent_turns[1].role == "assistant"


def test_extract_deduplicates_current_question():
    game = _game_with_messages([
        _msg("user", "Was ist Ananta?"),
        _msg("ai", "Ein System."),
        _msg("user", "Wie funktioniert es?"),
    ])
    ctx = extract_memory_context(game, current_question="Wie funktioniert es?")
    users = [t for t in ctx.recent_turns if t.role == "user"]
    assert all("Wie funktioniert" not in t.content for t in users)


def test_extract_respects_max_turns():
    messages = []
    for i in range(20):
        messages.append(_msg("user", f"Frage {i}"))
        messages.append(_msg("ai", f"Antwort {i}"))
    game = _game_with_messages(messages)
    ctx = extract_memory_context(game, max_turns=6)
    assert len(ctx.recent_turns) <= 6


def test_extract_respects_char_budget():
    long_text = "x" * 500
    messages = [_msg("user", long_text), _msg("ai", long_text), _msg("user", long_text)]
    game = _game_with_messages(messages)
    ctx = extract_memory_context(game, max_chars=600)
    total = sum(len(t.content) for t in ctx.recent_turns)
    assert total <= 600 + 100  # small tolerance for last added turn


def test_extract_excludes_control_messages_by_default():
    game = _game_with_messages([
        _msg("control", "System message"),
        _msg("user", "User question"),
        _msg("ai", "AI answer"),
    ])
    ctx = extract_memory_context(game, include_control=False)
    roles = {t.role for t in ctx.recent_turns}
    assert "control" not in roles


def test_to_prior_messages():
    ctx = ChatMemoryContext(
        recent_turns=[MemoryTurn("user", "Hallo"), MemoryTurn("assistant", "Hi")],
    )
    msgs = ctx.to_prior_messages()
    assert msgs == [{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "Hi"}]


def test_serializable():
    ctx = ChatMemoryContext(
        recent_turns=[MemoryTurn("user", "test")],
        rolling_summary="summary here",
    )
    d = ctx.serializable()
    assert "recent_turns" in d
    assert d["rolling_summary"] == "summary here"


# ── Rolling Summary ───────────────────────────────────────────────────────────

def test_rolling_summary_empty_initially():
    assert get_rolling_summary({}) == ""


def test_set_and_get_rolling_summary():
    game: dict = {}
    set_rolling_summary(game, "summary text")
    assert get_rolling_summary(game) == "summary text"


def test_update_rolling_summary_appends():
    game: dict = {}
    update_rolling_summary(game, last_question="Was?", last_answer="Das.", max_chars=500)
    summary = get_rolling_summary(game)
    assert "Was?" in summary
    assert "Das." in summary


def test_update_rolling_summary_trims_to_max_chars():
    game: dict = {}
    for i in range(50):
        update_rolling_summary(game, last_question=f"Frage {i}", last_answer=f"Antwort {i}", max_chars=200)
    assert len(get_rolling_summary(game)) <= 200


def test_update_rolling_summary_every_k_turns():
    game: dict = {}
    update_rolling_summary(game, last_question="Q1", last_answer="A1", update_every_turns=3)
    s1 = get_rolling_summary(game)
    update_rolling_summary(game, last_question="Q2", last_answer="A2", update_every_turns=3)
    s2 = get_rolling_summary(game)
    assert s1 == s2  # turn 2 doesn't update (3 != multiple of 3)


def test_update_rolling_summary_no_crash_on_empty():
    game: dict = {}
    result = update_rolling_summary(game, last_question="", last_answer="", max_chars=100)
    assert isinstance(result, str)


# ── Memory Settings ───────────────────────────────────────────────────────────

def test_resolve_memory_settings_defaults():
    s = resolve_memory_settings({})
    assert s["use_history"] is True
    assert s["history_turns"] == 6
    assert s["history_chars"] == 1800
    assert s["use_summary"] is True
    assert s["pass_memory_to_worker"] is True
    assert s["backend_fallback"] == "lmstudio"


def test_resolve_memory_settings_from_game():
    game = {
        "chat_use_history": False,
        "chat_history_turns": 10,
        "chat_history_chars": 3000,
        "chat_use_summary": False,
        "chat_pass_memory_to_worker": False,
        "chat_backend_fallback": "none",
    }
    s = resolve_memory_settings(game)
    assert s["use_history"] is False
    assert s["history_turns"] == 10
    assert s["history_chars"] == 3000
    assert s["use_summary"] is False
    assert s["pass_memory_to_worker"] is False
    assert s["backend_fallback"] == "none"


def test_diagnostics_keys():
    ctx = ChatMemoryContext(recent_turns=[MemoryTurn("user", "test")])
    d = ctx.diagnostics()
    assert "recent_turns" in d
    assert "rolling_summary_chars" in d
