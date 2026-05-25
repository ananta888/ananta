from __future__ import annotations

from pathlib import Path

from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import OperatorState


def test_artifact_prompt_contains_bounded_context(tmp_path: Path) -> None:
    f = tmp_path / "artifact.py"
    f.write_text("\n".join(f"line {i}" for i in range(40)), encoding="utf-8")
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    game = {
        "artifact_intent_target": {
            "label": "artifact.py",
            "payload": {"path": str(f)},
        }
    }
    overlay = tui._artifact_chat_prompt_overlay(game=game)
    assert "artifact_context=artifact.py" in overlay
    assert "line 0" in overlay


def test_large_artifact_context_is_truncated(tmp_path: Path) -> None:
    f = tmp_path / "artifact.py"
    f.write_text("x" * 3000, encoding="utf-8")
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    overlay = tui._artifact_chat_prompt_overlay(
        game={"artifact_intent_target": {"label": "artifact.py", "payload": {"path": str(f)}}}
    )
    assert len(overlay) < 700


def test_policy_blocks_disallowed_backend(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_BACKEND", "none")
    tip = tui._tutorial_ai_worker_propose_message(now=1.0, status="s", hints=[], rag_context=[])
    assert tip is None
