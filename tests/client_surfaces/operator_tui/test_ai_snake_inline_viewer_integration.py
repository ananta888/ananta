from __future__ import annotations

from pathlib import Path

from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.region_index import RegionTarget


def test_confirmed_artifact_opens_inline_viewer(tmp_path: Path) -> None:
    sample = tmp_path / "artifact.txt"
    sample.write_text("hello\nworld\n", encoding="utf-8")
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000", section_id="artifacts"))
    target = RegionTarget(
        kind="artifact",
        section_id="artifacts",
        pane="content",
        label="artifact.txt",
        payload={"path": str(sample), "id": "a1"},
    )
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    tui._activate_artifact_chat(game, target=target, now=1.0)
    tui._open_artifact_target_inline(target=target)
    assert "Inline Vim Viewer" in tui.state.markdown_source


def test_binary_artifact_uses_metadata_only_explanation(tmp_path: Path) -> None:
    binary = tmp_path / "artifact.bin"
    binary.write_bytes(b"\x00\x01\x02")
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000", section_id="artifacts"))
    overlay = tui._artifact_chat_prompt_overlay(
        game={"artifact_intent_target": {"label": "artifact.bin", "payload": {"path": str(binary)}}}
    )
    assert "artifact_context=artifact.bin" in overlay


def test_missing_artifact_shows_error() -> None:
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))
    target = RegionTarget(
        kind="artifact",
        section_id="artifacts",
        pane="content",
        label="missing",
        payload={"path": "/tmp/definitely-missing-nope-42.txt"},
    )
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    tui._activate_artifact_chat(game, target=target, now=2.0)
    tui._open_artifact_target_inline(target=target)
    assert "Inline Vim Viewer" not in tui.state.markdown_source
