from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from client_surfaces.operator_tui.config.user_config_manager import (
    SCHEMA_VERSION,
    UserConfigManager,
    _DEFAULTS,
    _SCHEMA_KEYS,
    _validated,
    flush_user_config,
    global_config_path,
    load_user_config,
    project_config_path,
    reset_manager,
    save_user_config,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_manager()
    yield
    reset_manager()


# ── Paths ─────────────────────────────────────────────────────────────────────

def test_global_config_path():
    p = global_config_path()
    assert p.name == "user.json"
    assert ".anana" in str(p)


def test_project_config_path(tmp_path):
    p = project_config_path(tmp_path)
    assert p == tmp_path / "user.json"


# ── Load with defaults ────────────────────────────────────────────────────────

def test_load_returns_defaults_when_no_files(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr._global_path = tmp_path / "nonexistent_global.json"  # no real ~/.anana
    settings = mgr.load()
    assert settings["chat_backend"] == _DEFAULTS["chat_backend"]
    assert settings["chat_use_history"] == _DEFAULTS["chat_use_history"]


def test_load_merges_global_then_project(tmp_path):
    global_dir = tmp_path / "home" / ".anana"
    global_dir.mkdir(parents=True)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Write global: chat_backend=lmstudio
    (global_dir / "user.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "settings": {"chat_backend": "lmstudio"}}),
        encoding="utf-8",
    )
    # Write project: chat_backend=opencode (overrides global)
    (project_dir / "user.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "settings": {"chat_backend": "opencode"}}),
        encoding="utf-8",
    )

    mgr = UserConfigManager.__new__(UserConfigManager)
    mgr._cwd = project_dir.resolve()
    mgr._global_path = global_dir / "user.json"
    mgr._project_path = project_dir / "user.json"
    mgr._cache = {}
    mgr._dirty = False

    settings = mgr.load()
    assert settings["chat_backend"] == "opencode"


def test_load_tolerates_corrupted_file(tmp_path):
    (tmp_path / "user.json").write_text("NOT VALID JSON", encoding="utf-8")
    mgr = UserConfigManager(cwd=tmp_path)
    settings = mgr.load()
    assert "chat_backend" in settings


def test_load_tolerates_missing_settings_key(tmp_path):
    (tmp_path / "user.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8"
    )
    mgr = UserConfigManager(cwd=tmp_path)
    settings = mgr.load()
    assert "chat_backend" in settings


# ── Atomic write ──────────────────────────────────────────────────────────────

def test_save_writes_project_file(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "lmstudio"})
    project_file = tmp_path / "user.json"
    assert project_file.exists()
    data = json.loads(project_file.read_text())
    assert data["settings"]["chat_backend"] == "lmstudio"
    assert data["schema_version"] == SCHEMA_VERSION


def test_save_does_not_leave_tmp_file(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "lmstudio"})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_save_produces_valid_json(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "hermes", "chat_max_tokens": 800})
    data = json.loads((tmp_path / "user.json").read_text())
    assert isinstance(data, dict)
    assert data["settings"]["chat_max_tokens"] == 800


def test_save_round_trip(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "opencode", "chat_history_turns": 10})
    mgr2 = UserConfigManager(cwd=tmp_path)
    loaded = mgr2.load()
    assert loaded["chat_backend"] == "opencode"
    assert loaded["chat_history_turns"] == 10


# ── Schema validation ─────────────────────────────────────────────────────────

def test_validated_strips_unknown_keys():
    result = _validated({"chat_backend": "lmstudio", "unknown_key_xyz": "bad"})
    assert "unknown_key_xyz" not in result
    assert "chat_backend" in result


def test_validated_strips_non_primitive_values():
    result = _validated({"chat_backend": "lmstudio", "chat_history_turns": [1, 2, 3]})
    assert "chat_history_turns" not in result


def test_schema_keys_match_persistent_keys():
    from client_surfaces.operator_tui.ai_snake_config_view import _PERSISTENT_TUI_CONFIG_KEYS
    missing_from_schema = _PERSISTENT_TUI_CONFIG_KEYS - _SCHEMA_KEYS
    assert missing_from_schema == set(), f"Keys in PERSISTENT but not SCHEMA: {missing_from_schema}"


# ── save_from_game ────────────────────────────────────────────────────────────

def test_save_from_game_extracts_persistent_keys(tmp_path):
    game = {
        "chat_backend": "lmstudio",
        "tutorial_mode": True,
        "non_persistent_key": "ignored",
        "chat_history_turns": 12,
    }
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save_from_game(game)
    data = json.loads((tmp_path / "user.json").read_text())
    assert data["settings"]["chat_backend"] == "lmstudio"
    assert data["settings"]["chat_history_turns"] == 12
    assert "non_persistent_key" not in data["settings"]


# ── flush ─────────────────────────────────────────────────────────────────────

def test_flush_writes_project_and_global(tmp_path):
    global_path = tmp_path / "global" / "user.json"
    project_path = tmp_path / "project"
    project_path.mkdir()

    mgr = UserConfigManager.__new__(UserConfigManager)
    mgr._cwd = project_path.resolve()
    mgr._global_path = global_path
    mgr._project_path = project_path / "user.json"
    mgr._cache = {}
    mgr._dirty = False

    game = {"chat_backend": "hermes", "chat_max_tokens": 1200}
    p_ok, g_ok = mgr.flush(game)
    assert p_ok is True
    assert g_ok is True
    assert (project_path / "user.json").exists()
    assert global_path.exists()


def test_flush_user_config_helper(tmp_path):
    reset_manager()
    game = {"chat_backend": "lmstudio", "chat_history_turns": 8}
    p_ok, g_ok = flush_user_config(game, cwd=tmp_path)
    assert p_ok is True


# ── apply_to_game ─────────────────────────────────────────────────────────────

def test_apply_to_game_fills_missing_keys(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "opencode"})
    game: dict = {}
    updated = mgr.apply_to_game(game)
    assert updated["chat_backend"] == "opencode"


def test_apply_to_game_does_not_overwrite_existing(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "opencode"})
    game = {"chat_backend": "lmstudio"}
    updated = mgr.apply_to_game(game)
    assert updated["chat_backend"] == "lmstudio"


# ── diagnostics ───────────────────────────────────────────────────────────────

def test_diagnostics_keys(tmp_path):
    mgr = UserConfigManager(cwd=tmp_path)
    d = mgr.diagnostics()
    assert "global_path" in d
    assert "project_path" in d
    assert "schema_version" in d
    assert d["schema_version"] == SCHEMA_VERSION


# ── Concurrent/error safety ───────────────────────────────────────────────────

def test_save_with_unwritable_dir_does_not_crash(tmp_path):
    # Use a separate writable dir for global, read-only dir for project
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    bad_path = tmp_path / "readonly_dir"
    bad_path.mkdir()
    bad_path.chmod(0o444)
    mgr = UserConfigManager.__new__(UserConfigManager)
    mgr._cwd = bad_path.resolve()
    mgr._global_path = global_dir / "user.json"  # writable
    mgr._project_path = bad_path / "user.json"   # read-only
    mgr._cache = {}
    mgr._dirty = False
    try:
        result = mgr.save({"chat_backend": "lmstudio"})
        # Should return False on failure, not raise
        assert isinstance(result, bool)
    finally:
        bad_path.chmod(0o755)


def test_save_with_corrupted_existing_file(tmp_path):
    (tmp_path / "user.json").write_text("CORRUPTED", encoding="utf-8")
    mgr = UserConfigManager(cwd=tmp_path)
    mgr.save({"chat_backend": "lmstudio"})
    data = json.loads((tmp_path / "user.json").read_text())
    assert data["settings"]["chat_backend"] == "lmstudio"
