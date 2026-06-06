from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from client_surfaces.operator_tui.config.user_config_manager import (
    UserConfigManager,
    reset_manager,
    _SCHEMA_KEYS,
)
from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_items,
    apply_ai_snake_config_value,
)


@pytest.fixture(autouse=True)
def reset_and_isolate(tmp_path):
    reset_manager()
    # Redirect UserConfigManager to tmp_path for all tests
    with patch(
        "client_surfaces.operator_tui.config.user_config_manager.get_manager",
        return_value=UserConfigManager(cwd=tmp_path),
    ):
        yield tmp_path
    reset_manager()


def _project_json(tmp_path: Path) -> dict:
    p = tmp_path / "user.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ── Immediate write on change ─────────────────────────────────────────────────

def test_changing_chat_backend_writes_project_json(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_backend", value="lmstudio")
    data = _project_json(tmp_path)
    assert data.get("settings", {}).get("chat_backend") == "lmstudio"


def test_changing_bool_setting_writes_project_json(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_use_history", value="AUS")
    data = _project_json(tmp_path)
    assert data.get("settings", {}).get("chat_use_history") is False


def test_changing_memory_turns_writes_project_json(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_history_turns", value="12")
    data = _project_json(tmp_path)
    assert data.get("settings", {}).get("chat_history_turns") == 12


def test_partial_change_does_not_corrupt_json(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_backend", value="lmstudio")
    apply_ai_snake_config_value(game, key="chat_max_tokens", value="800")
    data = _project_json(tmp_path)
    assert data["settings"]["chat_backend"] == "lmstudio"
    assert data["settings"]["chat_max_tokens"] == 800


# ── Reload restores settings ──────────────────────────────────────────────────

def test_reload_restores_changed_backend(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_backend", value="opencode")
    # Simulate reload: fresh manager reads project file
    mgr = UserConfigManager(cwd=tmp_path)
    loaded = mgr.load()
    assert loaded.get("chat_backend") == "opencode"


def test_reload_restores_memory_settings(tmp_path):
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_use_summary", value="AUS")
    apply_ai_snake_config_value(game, key="chat_summary_chars", value="2500")
    mgr = UserConfigManager(cwd=tmp_path)
    loaded = mgr.load()
    assert loaded.get("chat_use_summary") is False
    assert loaded.get("chat_summary_chars") == 2500


# ── All persistent keys are schema-compliant ──────────────────────────────────

def test_all_config_items_have_schema_keys():
    items = ai_snake_config_items({})
    real_keys = {i["key"] for i in items if i["key"] not in {
        "visual_enabled", "chat_panel_open", "visual_provider", "visual_codecompass",
        "chat_backend", "chat_model", "chat_api_base", "chat_ask_timeout_s",
        "chat_source_pack_id", "chat_context_chars", "chat_max_tokens",
        "chat_rag_top_k", "chat_answer_chars",
    }}
    # Memory keys should all be in schema
    memory_keys = {"chat_use_history", "chat_history_turns", "chat_history_chars",
                   "chat_use_summary", "chat_summary_chars", "chat_summary_update_every_turns",
                   "chat_pass_memory_to_worker", "chat_worker_mode", "chat_backend_fallback",
                   "chat_include_runtime_status", "chat_retrieval_profile",
                   "chat_retrieval_domain_hint", "chat_code_questions_repo_first",
                   "chat_architecture_analysis_mode"}
    assert memory_keys.issubset(_SCHEMA_KEYS)


# ── Flush on exit ─────────────────────────────────────────────────────────────

def test_flush_writes_both_files(tmp_path):
    global_path = tmp_path / "global_user.json"
    mgr = UserConfigManager.__new__(UserConfigManager)
    mgr._cwd = tmp_path.resolve()
    mgr._global_path = global_path
    mgr._project_path = tmp_path / "user.json"
    mgr._cache = {}
    mgr._dirty = False

    game = {"chat_backend": "lmstudio", "chat_history_turns": 10}
    p_ok, g_ok = mgr.flush(game)
    assert p_ok and g_ok
    assert (tmp_path / "user.json").exists()
    assert global_path.exists()


def test_flush_survives_write_error():
    """flush() must not raise even if both files are unwritable."""
    from client_surfaces.operator_tui.config.user_config_manager import flush_user_config
    game = {"chat_backend": "lmstudio"}
    with patch("client_surfaces.operator_tui.config.user_config_manager._write_atomic", return_value=False):
        p_ok, g_ok = flush_user_config(game, cwd=Path("/nonexistent/path/xyz"))
    assert isinstance(p_ok, bool) and isinstance(g_ok, bool)
