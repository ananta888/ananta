from __future__ import annotations

import json
import os
import pytest

from agent.services.tui_tool_registry import (
    TuiConfigValidationError,
    TuiToolRegistry,
    _GLOBAL_DEFAULT_CONFIG,
    _parse_config,
)


# ── _parse_config unit tests ──────────────────────────────────────────────────

def test_parse_global_defaults():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    assert cfg.default_editor == "vim"
    assert "vim" in cfg.allowed_tools
    assert "nvim" in cfg.allowed_tools
    assert cfg.allow_environment_editor is True


def test_parse_custom_default_editor():
    raw = dict(_GLOBAL_DEFAULT_CONFIG)
    raw["default_editor"] = "nvim"
    cfg = _parse_config(raw)
    assert cfg.default_editor == "nvim"


def test_parse_unknown_default_editor_raises():
    raw = dict(_GLOBAL_DEFAULT_CONFIG)
    raw["default_editor"] = "emacs"
    with pytest.raises(TuiConfigValidationError, match="default_editor"):
        _parse_config(raw)


def test_parse_unknown_editor_in_filetype_rules_raises():
    raw = dict(_GLOBAL_DEFAULT_CONFIG)
    raw = {**raw, "filetype_editors": [{"match": "*.py", "editor": "emacs", "args": ["{file}"]}]}
    with pytest.raises(TuiConfigValidationError, match="emacs"):
        _parse_config(raw)


def test_parse_tool_profile_unknown_command_raises():
    raw = {**dict(_GLOBAL_DEFAULT_CONFIG), "tool_profiles": [{"id": "x", "command": "emacs", "args": []}]}
    with pytest.raises(TuiConfigValidationError, match="emacs"):
        _parse_config(raw)


def test_parse_allowed_tools_empty_falls_back_to_defaults():
    raw = {**dict(_GLOBAL_DEFAULT_CONFIG), "allowed_tools": []}
    cfg = _parse_config(raw)
    assert "vim" in cfg.allowed_tools


def test_parse_tool_name_with_path_separator_raises():
    raw = {**dict(_GLOBAL_DEFAULT_CONFIG), "allowed_tools": ["vim", "/usr/bin/nvim"]}
    with pytest.raises(TuiConfigValidationError, match="path separators"):
        _parse_config(raw)


def test_parse_filetype_rules_populated():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    patterns = [r.match for r in cfg.filetype_rules]
    assert "*.md" in patterns
    assert "*.json" in patterns


def test_editor_profiles_built_for_all_allowed_tools():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    for tool in cfg.allowed_tools:
        assert tool in cfg.editor_profiles, f"Missing editor profile for {tool}"


def test_vim_readonly_supported():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    assert cfg.editor_profiles["vim"].readonly_supported is True
    assert "-R" in cfg.editor_profiles["vim"].readonly_extra_args


def test_nano_readonly_supported():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    assert cfg.editor_profiles["nano"].readonly_supported is True
    assert "-v" in cfg.editor_profiles["nano"].readonly_extra_args


def test_micro_readonly_not_supported():
    cfg = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
    assert cfg.editor_profiles["micro"].readonly_supported is False


# ── TuiToolRegistry integration tests ────────────────────────────────────────

def test_registry_loads_global_defaults_when_no_files(tmp_path):
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "nonexistent.json"),
        project_config_path=str(tmp_path / "also_nonexistent.json"),
    )
    cfg = registry.load()
    assert cfg.default_editor == "vim"
    assert "vim" in cfg.allowed_tools


def test_registry_project_config_overrides_default_editor(tmp_path):
    project_cfg = {"default_editor": "nvim", "allowed_tools": list(_GLOBAL_DEFAULT_CONFIG["allowed_tools"])}
    cfg_file = tmp_path / "tui-tools.json"
    cfg_file.write_text(json.dumps(project_cfg))
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "nonexistent.json"),
        project_config_path=str(cfg_file),
    )
    cfg = registry.load()
    assert cfg.default_editor == "nvim"


def test_registry_user_config_overrides_global_but_project_wins(tmp_path):
    user_cfg = {"default_editor": "nano", "allowed_tools": list(_GLOBAL_DEFAULT_CONFIG["allowed_tools"])}
    project_cfg = {"default_editor": "nvim", "allowed_tools": list(_GLOBAL_DEFAULT_CONFIG["allowed_tools"])}
    user_file = tmp_path / "user.json"
    project_file = tmp_path / "project.json"
    user_file.write_text(json.dumps(user_cfg))
    project_file.write_text(json.dumps(project_cfg))
    registry = TuiToolRegistry(user_config_path=str(user_file), project_config_path=str(project_file))
    cfg = registry.load()
    assert cfg.default_editor == "nvim"  # project beats user


def test_registry_invalid_project_config_falls_back_to_defaults(tmp_path):
    bad_file = tmp_path / "tui-tools.json"
    bad_file.write_text(json.dumps({"default_editor": "emacs", "allowed_tools": ["vim"]}))
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "none.json"),
        project_config_path=str(bad_file),
    )
    cfg = registry.load()
    # emacs not in allowed_tools → validation error → fallback to global defaults
    assert cfg.default_editor == "vim"


def test_registry_reload_picks_up_new_file(tmp_path):
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "none.json"),
        project_config_path=str(tmp_path / "tui-tools.json"),
    )
    cfg1 = registry.load()
    assert cfg1.default_editor == "vim"

    (tmp_path / "tui-tools.json").write_text(
        json.dumps({"default_editor": "nano", "allowed_tools": list(_GLOBAL_DEFAULT_CONFIG["allowed_tools"])})
    )
    cfg2 = registry.reload()
    assert cfg2.default_editor == "nano"


def test_registry_is_allowed_tool(tmp_path):
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "none.json"),
        project_config_path=str(tmp_path / "none.json"),
    )
    assert registry.is_allowed_tool("vim") is True
    assert registry.is_allowed_tool("emacs") is False
    assert registry.is_allowed_tool("") is False


def test_registry_get_tool_profile(tmp_path):
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "none.json"),
        project_config_path=str(tmp_path / "none.json"),
    )
    profile = registry.get_tool_profile("git_ui")
    assert profile is not None
    assert profile.command == "lazygit"


def test_registry_get_unknown_tool_profile_returns_none(tmp_path):
    registry = TuiToolRegistry(
        user_config_path=str(tmp_path / "none.json"),
        project_config_path=str(tmp_path / "none.json"),
    )
    assert registry.get_tool_profile("nonexistent") is None
