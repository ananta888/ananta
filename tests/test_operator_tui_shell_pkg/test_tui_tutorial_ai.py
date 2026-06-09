from __future__ import annotations

import json
import os
import re
import sys
import time
from importlib import reload
from argparse import Namespace
from pathlib import Path

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_items,
    apply_ai_snake_config_value,
    chat_model_option_label,
    refresh_chat_backend_models,
)
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.capabilities import graphics_decision
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState, SectionLoadResult
from client_surfaces.operator_tui.performance import measure
from client_surfaces.operator_tui.read_models import build_goal_rows, build_task_rows
from client_surfaces.operator_tui.refresh import refresh_policy_for, should_refresh
from client_surfaces.operator_tui.renderer import _overlay_fullscreen_snake, render_operator_shell
from client_surfaces.operator_tui.rollout import operator_tui_enabled, rollback_hint, rollout_stage
from client_surfaces.operator_tui.sections import SECTIONS, move_section, normalize_section_id
from client_surfaces.operator_tui.smoke import run_fixture_smoke
from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings, save_tui_chat_settings
from client_surfaces.operator_tui.chat_state import sanitize_text
from agent.cli.main import _run_tui


def test_tutorial_ai_toggle_changes_mode_flag() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)
    tui._toggle_snake_mode()

    initial = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    tui._toggle_tutorial_ai_mode()
    toggled = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    assert toggled is (not initial)

    tui._toggle_tutorial_ai_mode()
    restored = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    assert restored is initial



def test_tutorial_ai_snake_is_added_with_knowledge_message() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(6, 3), (5, 3), (4, 3)],
        "trail_path": [(6, 3), (5, 3), (4, 3)],
        "mark_cells": [],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "vel_x": 0.0,
        "vel_y": 0.0,
        "accum_x": 0.0,
        "accum_y": 0.0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snakes = ((tui.state.header_logo_game or {}).get("snakes") or {})
    ai = snakes.get("s-ai") if isinstance(snakes, dict) else None
    assert isinstance(ai, dict)
    assert ai.get("pseudonym") == "tutor-ai"
    assert str(ai.get("message") or "") != ""
    assert ai.get("oidc_provider") == "codecompass-ai"



def test_tutorial_ai_tip_uses_codecompass_hints_when_available(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "1")
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["method · plan_tasks · client_surfaces/operator_tui/interactive.py"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: [])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: None)

    tip = tui._tutorial_ai_tip(now=1.0)

    assert "CodeCompass:" in tip
    assert "mode=normal" in tip
    assert "section=tasks" in tip



def test_tutorial_ai_tip_prefers_llm_when_available(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["hint-a"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["rag-context"])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: "LLM tutor hint")

    tip = tui._tutorial_ai_tip(now=2.0)

    assert tip == "LLM tutor hint"



def test_tutorial_ai_llm_message_reads_openai_compatible_endpoint(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)

    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", "gpt-test")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", "secret-token")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"Use [Tab] to switch focus."}}]}'

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Use [Tab] to switch focus."



def test_tutorial_ai_llm_message_uses_lmstudio_defaults_without_token(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"Use :inspect for details."}}]}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers.get("Authorization")
        captured["body"] = req.data.decode("utf-8")
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", _fake_urlopen)

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Use :inspect for details."
    assert captured["url"] == "http://192.168.178.100:1234/v1/chat/completions"
    assert captured["authorization"] in {None, ""}
    assert '"model": "google/gemma-4-e4b"' in captured["body"]



def test_tutorial_ai_llm_training_mode_selects_tagged_profile(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", "meta-llama_-_llama-3.2-1b-instruct")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_TRAINING", "1")

    calls = {"count": 0}

    class _FakeResp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._payload

    def _fake_urlopen(req, timeout=0):
        calls["count"] += 1
        body = req.data.decode("utf-8")
        if "steering prefix" in body:
            return _FakeResp(b'{"choices":[{"message":{"content":"[target=nav] Open tasks and inspect queue."}}]}')
        return _FakeResp(b'{"choices":[{"message":{"content":"Open tasks and inspect queue."}}]}')

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", _fake_urlopen)

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Open tasks and inspect queue."
    assert tui._tutorial_worker_target_hint == "nav"
    assert tui._tutorial_last_target == "nav"
    assert calls["count"] >= 3



def test_tutorial_ai_tip_async_mode_keeps_ui_responsive(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="dashboard")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_tutorial_async_enabled", lambda: True)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["queue depth"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["tasks pending"])
    monkeypatch.setattr(tui, "_tutorial_ai_worker_propose_message", lambda now, status, hints, rag_context: None)

    def _slow_llm(*, now: float, status: str, hints: list[str]) -> str | None:
        time.sleep(0.15)
        tui._tutorial_worker_target_hint = "nav"
        return "Open tasks and inspect queue."

    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", _slow_llm)

    first_tip = tui._tutorial_ai_tip(now=1.0)
    assert "analysiert UI-Delta" in first_tip

    time.sleep(0.2)
    second_tip = tui._tutorial_ai_tip(now=2.0)
    assert "Open tasks and inspect queue." in second_tip
    assert tui._tutorial_last_target == "nav"



def test_tutorial_ai_worker_propose_message_reads_step_propose(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_BACKEND", "worker-propose")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"success","data":{"reason":"[target=nav] Open tasks and inspect failed item first."}}'

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())

    tip = tui._tutorial_ai_worker_propose_message(
        now=1.0,
        status="status",
        hints=["hint-a"],
        rag_context=["rag-a"],
    )

    assert tip == "Open tasks and inspect failed item first."
    assert tui._tutorial_worker_target_hint == "nav"



def test_tutorial_ai_tip_includes_rag_helper_context_when_llm_missing(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "1")
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: [])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["architecture · Hub owns routing"])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: None)

    tip = tui._tutorial_ai_tip(now=2.0)

    assert "RAG:" in tip
    assert "Hub owns routing" in tip



def test_tutorial_ai_target_cell_prefers_header_for_auth_context() -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)

    target = tui._tutorial_ai_target_cell(
        board_w=120,
        board_h=30,
        context_tokens=["auth endpoint oidc header"],
        local_head=(50, 12),
    )

    assert target[0] >= 90
    assert target[1] <= 6



def test_tutorial_ai_snake_moves_toward_context_target(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(10, 10), (9, 10), (8, 10)],
        "trail_path": [(10, 10), (9, 10), (8, 10)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["auth endpoint header"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["oidc configuration"])
    monkeypatch.setattr(tui, "_tutorial_ai_tip", lambda now: "tip")

    snakes = {
        "s1": {
            "id": "s1",
            "snake": [(10, 10), (9, 10)],
            "trail_path": [(10, 10), (9, 10)],
            "message": "",
            "snake_color": "mint",
        }
    }
    tui._update_tutorial_ai_snake(game, snakes, now=10.0, board_w=120, board_h=30, enabled=True)

    ai = snakes.get("s-ai")
    assert isinstance(ai, dict)
    head = (ai.get("snake") or [(-1, -1)])[0]
    target = ai.get("target_cell")
    assert isinstance(target, tuple)
    assert isinstance(head, tuple)
    assert head[0] >= 100



def test_tutorial_ai_propose_history_is_recorded_in_game_state(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(10, 10), (9, 10), (8, 10)],
        "trail_path": [(10, 10), (9, 10), (8, 10)],
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["task navigation"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["queue status"])
    monkeypatch.setattr(tui, "_tutorial_ai_tip", lambda now: "Open tasks and inspect queue.")
    tui._tutorial_last_source = "worker-propose"
    tui._tutorial_last_target = "nav"

    snakes = {
        "s1": {
            "id": "s1",
            "snake": [(10, 10), (9, 10)],
            "trail_path": [(10, 10), (9, 10)],
            "message": "",
            "snake_color": "mint",
        }
    }
    tui._update_tutorial_ai_snake(game, snakes, now=4.0, board_w=120, board_h=30, enabled=True)

    history = game.get("tutorial_propose_history")
    assert isinstance(history, list)
    assert history
    latest = history[-1]
    assert isinstance(latest, dict)
    assert latest.get("source") == "worker-propose"
    assert latest.get("target") == "nav"
    assert "inspect queue" in str(latest.get("text") or "")



def test_tutorial_ai_tip_sync_includes_user_feed_and_contact_zone(monkeypatch) -> None:
    game = {
        "active": True,
        "tutorial_mode": True,
        "tutorial_user_feed": "Explain authentication panel",
        "tutorial_ai_local_contact": True,
        "tutorial_ai_contact_zone": "header",
        "tutorial_prompt_template": "Priority={priority}; Feed={user_feed}; Zone={contact_zone}",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="dashboard", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    captured = {}

    def _fake_llm(*, now: float, status: str, hints: list[str]) -> str | None:
        captured["status"] = status
        return None

    monkeypatch.setattr(tui, "_tutorial_ai_worker_propose_message", lambda now, status, hints, rag_context: None)
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", _fake_llm)

    result = tui._tutorial_ai_tip_sync(now=1.0, status="base-status", hints=["h1"], rag_context=["r1"])

    assert result["source"] == "codecompass-rag"
    status = str(captured.get("status") or "")
    assert "Feed=Explain authentication panel" in status
    assert "Zone=header" in status
    assert "Priority=explain-current-position" in status



def test_tutorial_rag_context_prefers_operator_tui_graph_and_embedding_records(tmp_path, monkeypatch) -> None:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.jsonl").write_text(
        json.dumps({"kind": "function_symbol", "file": "client_surfaces/operator_tui/interactive.py", "name": "_tutorial_ai_tip"}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "embedding.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "embedding_record",
                        "file": "client_surfaces/operator_tui/interactive.py",
                        "embedding_text": "snake prompt template propose flow",
                    }
                ),
                json.dumps(
                    {
                        "kind": "embedding_record",
                        "file": "src/unrelated.py",
                        "embedding_text": "completely unrelated",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "graph_nodes.jsonl").write_text(
        json.dumps(
            {
                "kind": "graph_node",
                "name": "_update_tutorial_ai_snake",
                "file": "client_surfaces/operator_tui/interactive.py",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "graph_edges.jsonl").write_text(
        json.dumps(
            {
                "kind": "graph_edge",
                "relation": "calls",
                "source_path": "client_surfaces/operator_tui/interactive.py",
                "target_path": "client_surfaces/operator_tui/renderer.py",
                "source_id": "n1",
                "target_id": "n2",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "partitioned_outputs": {
                    "embedding": ["embedding.jsonl"],
                    "graph_nodes": ["graph_nodes.jsonl"],
                    "graph_edges": ["graph_edges.jsonl"],
                }
            }
        ),
        encoding="utf-8",
    )

    from client_surfaces.operator_tui.tutorial_ai_mixin import _load_rag_context_from_dir

    query_tokens = ["snake", "prompt", "flow", "explain"]
    context = _load_rag_context_from_dir(out_dir, query_tokens, 48, 800)

    joined = "\n".join(context)
    assert "embedding" in joined
    assert "graph_nodes" in joined
    assert "graph_edges" in joined
    assert "client_surfaces/operator_tui/interactive.py" in joined



def test_tutorial_ai_llm_ask_uses_chat_max_tokens_from_config(monkeypatch) -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_context_chars": 1200, "chat_max_tokens": 900},
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    captured: dict[str, object] = {}

    class _FakeResp:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(question="hi", context_text="ctx", depth="overview", prior_messages=[])

    assert answer == "ok"
    assert int((captured.get("body") or {}).get("max_tokens") or 0) == 900



def test_llm_ask_uses_lmstudio_defaults_without_explicit_env(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    for key in (
        "ANANTA_TUI_SNAKE_AI_MODEL",
        "ANANTA_TUI_SNAKE_AI_API_BASE_URL",
        "ANANTA_TUI_SNAKE_AI_API_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    captured: dict[str, object] = {}

    class _FakeResp:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(question="hi", context_text="", depth="overview", prior_messages=[])

    assert answer == "ok"
    assert captured["url"] == "http://192.168.178.100:1234/v1/chat/completions"
    assert captured["body"]["model"] == "google/gemma-4-e4b"


