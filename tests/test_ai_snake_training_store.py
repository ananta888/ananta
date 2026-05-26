from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_training_store import data_path_status, ensure_training_layout, training_paths
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def test_training_layout_uses_config_ai_snake_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = ensure_training_layout()
    assert str(paths["base_dir"]).endswith("/.config/ananta/ai_snake")
    assert paths["active_profile"].exists()
    assert paths["learned_patterns"].exists()
    assert paths["exports_dir"].exists()


def test_ai_data_path_command_shows_all_expected_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    result = execute_command(":ai data path", state)
    assert result.handled is True
    msg = result.state.status_message
    assert "ai-data base=" in msg
    assert "prediction_profile.active.json" in msg
    assert "prediction_events.jsonl" in msg
    assert "learned_patterns.json" in msg
    assert "exports=" in msg
    # also ensure path helper is stable and non-empty
    assert data_path_status()
    assert training_paths()["base_dir"].name == "ai_snake"
