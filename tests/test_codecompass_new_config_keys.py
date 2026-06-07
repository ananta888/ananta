"""Regression tests for the three new CodeCompass-integration keys:

  - chat_codecompass_trigger_mode (str, choice: 4 values)
  - chat_include_task_memory (bool, default True)
  - chat_retrieval_domain_hint (str, choice: 8 values incl. empty)

These tests verify the wire-up from ``_DEFAULTS`` through
``_validated`` / ``save`` / ``apply_to_game`` so we can detect a regression
where the schema forgets one of the new keys.
"""
from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("PYTEST_CURRENT_TEST", "1")

import pytest  # noqa: E402

from client_surfaces.operator_tui.config.user_config_manager import (  # noqa: E402
    _DEFAULTS,
    _SCHEMA_KEYS,
    _validated,
    UserConfigManager,
    reset_manager,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_manager()
    yield
    reset_manager()


# ── Schema membership ─────────────────────────────────────────────────────────

class TestNewKeysInSchema:
    """The 3 new keys must all be in _SCHEMA_KEYS — otherwise save() will
    silently drop them and the user toggles won't survive a restart."""

    def test_trigger_mode_in_schema(self):
        assert "chat_codecompass_trigger_mode" in _SCHEMA_KEYS

    def test_task_memory_in_schema(self):
        assert "chat_include_task_memory" in _SCHEMA_KEYS

    def test_domain_hint_in_schema(self):
        assert "chat_retrieval_domain_hint" in _SCHEMA_KEYS

    def test_trigger_mode_in_persistent_set(self):
        # Importing the renderer-side constant checks that the TUI side
        # also marks the key as persistable.
        from client_surfaces.operator_tui.ai_snake_config_view import _PERSISTENT_TUI_CONFIG_KEYS
        assert "chat_codecompass_trigger_mode" in _PERSISTENT_TUI_CONFIG_KEYS
        assert "chat_include_task_memory" in _PERSISTENT_TUI_CONFIG_KEYS
        assert "chat_retrieval_domain_hint" in _PERSISTENT_TUI_CONFIG_KEYS


# ── Defaults ─────────────────────────────────────────────────────────────────

class TestNewKeysDefaults:
    """The defaults must be safe: trigger_mode=auto, task_memory=True,
    domain_hint=''. These are the values we ship in user.json."""

    def test_trigger_mode_default(self):
        assert _DEFAULTS["chat_codecompass_trigger_mode"] == "auto"

    def test_task_memory_default(self):
        assert _DEFAULTS["chat_include_task_memory"] is True

    def test_domain_hint_default(self):
        assert _DEFAULTS["chat_retrieval_domain_hint"] == ""

    def test_load_uses_defaults(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr._global_path = tmp_path / "nonexistent_global.json"
        settings = mgr.load()
        assert settings["chat_codecompass_trigger_mode"] == "auto"
        assert settings["chat_include_task_memory"] is True
        assert settings["chat_retrieval_domain_hint"] == ""


# ── Save / Load roundtrip ────────────────────────────────────────────────────

class TestNewKeysRoundtrip:
    """save() + load() must preserve the new keys verbatim — including
    falsy values like 'disabled' and empty string."""

    def test_trigger_mode_roundtrip(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_codecompass_trigger_mode": "force_codecompass"})
        # Roundtrip via disk to catch silent schema-filter regressions.
        loaded = UserConfigManager(cwd=tmp_path).load()
        assert loaded.get("chat_codecompass_trigger_mode") == "force_codecompass"

    def test_task_memory_roundtrip_false(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_include_task_memory": False})
        loaded = UserConfigManager(cwd=tmp_path).load()
        assert loaded.get("chat_include_task_memory") is False

    def test_task_memory_roundtrip_true(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_include_task_memory": True})
        loaded = UserConfigManager(cwd=tmp_path).load()
        assert loaded.get("chat_include_task_memory") is True

    def test_domain_hint_roundtrip_empty(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_retrieval_domain_hint": ""})
        loaded = UserConfigManager(cwd=tmp_path).load()
        assert loaded.get("chat_retrieval_domain_hint") == ""

    def test_domain_hint_roundtrip_worker(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_retrieval_domain_hint": "worker"})
        loaded = UserConfigManager(cwd=tmp_path).load()
        assert loaded.get("chat_retrieval_domain_hint") == "worker"

    def test_all_three_keys_persist_together(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({
            "chat_codecompass_trigger_mode": "force_repo_first",
            "chat_include_task_memory": False,
            "chat_retrieval_domain_hint": "operator_tui",
        })
        # Read raw disk contents — this proves the keys made it into JSON
        # and that _validated() does not filter them out as "unknown".
        data = json.loads((tmp_path / "user.json").read_text())
        assert data["settings"]["chat_codecompass_trigger_mode"] == "force_repo_first"
        assert data["settings"]["chat_include_task_memory"] is False
        assert data["settings"]["chat_retrieval_domain_hint"] == "operator_tui"


# ── _validated() coercion safety ─────────────────────────────────────────────

class TestNewKeysValidated:
    """_validated() must not drop or mutate the new keys."""

    def test_validated_preserves_trigger_mode(self):
        out = _validated({"chat_codecompass_trigger_mode": "disabled"})
        assert out["chat_codecompass_trigger_mode"] == "disabled"

    def test_validated_preserves_task_memory(self):
        out = _validated({"chat_include_task_memory": False})
        assert out["chat_include_task_memory"] is False

    def test_validated_preserves_domain_hint(self):
        out = _validated({"chat_retrieval_domain_hint": "ops"})
        assert out["chat_retrieval_domain_hint"] == "ops"

    def test_validated_drops_unknown_key(self):
        # Sanity: an unknown key is filtered. This is the contract that
        # the existing PERSISTENT ⊆ SCHEMA test relies on.
        out = _validated({"chat_definitely_unknown": "x"})
        assert "chat_definitely_unknown" not in out


# ── apply_to_game ────────────────────────────────────────────────────────────

class TestNewKeysApplyToGame:
    """The keys must reach the runtime game state via apply_to_game()."""

    def test_apply_to_game_sets_trigger_mode(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_codecompass_trigger_mode": "force_codecompass"})
        game: dict = {}
        # apply_to_game returns the merged dict; it does NOT mutate the
        # input dict in place. The caller must use the return value.
        result = mgr.apply_to_game(game)
        assert result["chat_codecompass_trigger_mode"] == "force_codecompass"

    def test_apply_to_game_sets_task_memory(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_include_task_memory": False})
        game: dict = {}
        result = mgr.apply_to_game(game)
        assert result["chat_include_task_memory"] is False

    def test_apply_to_game_sets_domain_hint(self, tmp_path):
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_retrieval_domain_hint": "ai_snake"})
        game: dict = {}
        result = mgr.apply_to_game(game)
        assert result["chat_retrieval_domain_hint"] == "ai_snake"

    def test_apply_to_game_does_not_overwrite_existing_value(self, tmp_path):
        # CRPS-007 regression: an explicit game value must win over the
        # persisted value (e.g. a user toggled it at runtime since startup).
        mgr = UserConfigManager(cwd=tmp_path)
        mgr.save({"chat_codecompass_trigger_mode": "disabled"})
        game = {"chat_codecompass_trigger_mode": "force_codecompass"}
        result = mgr.apply_to_game(game)
        assert result["chat_codecompass_trigger_mode"] == "force_codecompass"
