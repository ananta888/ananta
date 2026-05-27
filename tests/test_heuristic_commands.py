"""Tests for Command Pattern — T03.05."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_commands import (
    AskScopeCommand,
    CommandResult,
    EclipseCommandAdapter,
    FollowWithDistanceCommand,
    LurkNearCommand,
    MockCommandAdapter,
    NoActionCommand,
    OpenChatCommand,
    OpenSourceRefCommand,
    ShowContextSummaryCommand,
    ShowHintCommand,
    TuiCommandAdapter,
    command_for_decision,
)


@pytest.fixture
def adapter():
    return MockCommandAdapter()


# ── CommandResult ─────────────────────────────────────────────────────────────

def test_command_result_ok():
    r = CommandResult.ok("done", key="val")
    assert r.success
    assert r.output["key"] == "val"


def test_command_result_fail():
    r = CommandResult.fail("oops")
    assert not r.success
    assert r.message == "oops"


# ── FollowWithDistanceCommand ─────────────────────────────────────────────────

def test_follow_moves_snake(adapter):
    cmd = FollowWithDistanceCommand(dx=1, dy=0)
    result = cmd.execute(adapter)
    assert result.success
    assert {"method": "move_snake", "dx": 1, "dy": 0} in adapter.calls


def test_follow_to_dict():
    d = FollowWithDistanceCommand(target_x=5, target_y=3, distance=4, dx=1, dy=0).to_dict()
    assert d["command"] == "follow_with_distance"
    assert d["dx"] == 1


# ── LurkNearCommand ───────────────────────────────────────────────────────────

def test_lurk_sets_lurk_mode(adapter):
    cmd = LurkNearCommand(zone="editor")
    result = cmd.execute(adapter)
    assert result.success
    assert any(c["method"] == "set_lurk_mode" for c in adapter.calls)


def test_lurk_to_dict():
    d = LurkNearCommand(zone="terminal").to_dict()
    assert d["command"] == "lurk_near"
    assert d["zone"] == "terminal"


# ── ShowHintCommand ───────────────────────────────────────────────────────────

def test_show_hint(adapter):
    cmd = ShowHintCommand(hint_text="hello", duration_ms=2000)
    cmd.execute(adapter)
    assert {"method": "show_hint", "text": "hello", "duration_ms": 2000} in adapter.calls


# ── OpenChatCommand ───────────────────────────────────────────────────────────

def test_open_chat(adapter):
    OpenChatCommand().execute(adapter)
    assert any(c["method"] == "open_chat" for c in adapter.calls)


# ── ShowContextSummaryCommand ─────────────────────────────────────────────────

def test_show_context_summary(adapter):
    cmd = ShowContextSummaryCommand(refs=["ref1", "ref2"])
    cmd.execute(adapter)
    call = next(c for c in adapter.calls if c["method"] == "show_context_summary")
    assert call["refs"] == ["ref1", "ref2"]


# ── OpenSourceRefCommand ──────────────────────────────────────────────────────

def test_open_source_ref(adapter):
    OpenSourceRefCommand(ref="src/main.py").execute(adapter)
    call = next(c for c in adapter.calls if c["method"] == "open_source_ref")
    assert call["ref"] == "src/main.py"


# ── AskScopeCommand ───────────────────────────────────────────────────────────

def test_ask_scope(adapter):
    AskScopeCommand().execute(adapter)
    assert any(c["method"] == "request_scope" for c in adapter.calls)


# ── NoActionCommand ───────────────────────────────────────────────────────────

def test_no_action_succeeds(adapter):
    result = NoActionCommand().execute(adapter)
    assert result.success
    assert adapter.calls == []


# ── undo is optional / returns False ─────────────────────────────────────────

def test_undo_returns_false(adapter):
    assert FollowWithDistanceCommand().undo(adapter) is False


# ── TuiCommandAdapter ─────────────────────────────────────────────────────────

def test_tui_adapter_move_snake():
    state: dict = {}
    adapter = TuiCommandAdapter(state)
    FollowWithDistanceCommand(dx=1, dy=0).execute(adapter)
    assert state["snake_dx"] == 1
    assert state["snake_dy"] == 0


def test_tui_adapter_open_chat():
    state: dict = {}
    adapter = TuiCommandAdapter(state)
    OpenChatCommand().execute(adapter)
    assert state["chat_focus"] is True


def test_tui_adapter_lurk_mode():
    state: dict = {}
    adapter = TuiCommandAdapter(state)
    LurkNearCommand(zone="editor").execute(adapter)
    assert state["lurk_mode"] is True


# ── EclipseCommandAdapter ─────────────────────────────────────────────────────

def test_eclipse_adapter_move_snake():
    adapter = EclipseCommandAdapter()
    FollowWithDistanceCommand(dx=-1, dy=0).execute(adapter)
    cmds = adapter.flush()
    assert any(c["type"] == "MOVE_SNAKE" and c["dx"] == -1 for c in cmds)


def test_eclipse_adapter_flush_clears():
    adapter = EclipseCommandAdapter()
    OpenChatCommand().execute(adapter)
    adapter.flush()
    assert adapter.flush() == []


# ── command_for_decision mapping ──────────────────────────────────────────────

def test_follow_maps_to_follow_command():
    result = DecisionResult.heuristic_follow(dx=1, dy=0)
    cmd = command_for_decision(result)
    assert isinstance(cmd, FollowWithDistanceCommand)
    assert cmd.dx == 1


def test_lurk_maps_to_lurk_command():
    result = DecisionResult.heuristic_lurk()
    cmd = command_for_decision(result)
    assert isinstance(cmd, LurkNearCommand)


def test_chat_with_refs_maps_to_context_summary():
    result = DecisionResult(
        action_kind="chat", confidence=0.9, source="heuristic",
        selected_context_refs=["ref1"]
    )
    cmd = command_for_decision(result)
    assert isinstance(cmd, ShowContextSummaryCommand)


def test_chat_without_refs_maps_to_open_chat():
    result = DecisionResult(action_kind="chat", confidence=0.5, source="heuristic")
    cmd = command_for_decision(result)
    assert isinstance(cmd, OpenChatCommand)


def test_no_action_maps_to_no_action():
    result = DecisionResult(action_kind="no_action", confidence=0.0, source="heuristic")
    cmd = command_for_decision(result)
    assert isinstance(cmd, NoActionCommand)


def test_policy_denied_maps_to_hint():
    result = DecisionResult(action_kind="policy_denied", confidence=1.0, source="heuristic")
    cmd = command_for_decision(result)
    assert isinstance(cmd, ShowHintCommand)
