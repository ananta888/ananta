from __future__ import annotations

import pytest

from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_items,
    apply_ai_snake_config_value,
)


def _keys(game: dict | None = None) -> set[str]:
    return {str(item.get("key")) for item in ai_snake_config_items(game or {})}


def test_history_keys_present():
    keys = _keys()
    assert "chat_use_history" in keys
    assert "chat_history_turns" in keys
    assert "chat_history_chars" in keys


def test_summary_keys_present():
    keys = _keys()
    assert "chat_use_summary" in keys
    assert "chat_summary_chars" in keys
    assert "chat_summary_update_every_turns" in keys


def test_worker_memory_keys_present():
    keys = _keys()
    assert "chat_pass_memory_to_worker" in keys
    assert "chat_worker_mode" in keys
    assert "chat_backend_fallback" in keys


def test_runtime_status_key_present():
    assert "chat_include_runtime_status" in _keys()


def test_apply_chat_use_history_on():
    game: dict = {}
    result = apply_ai_snake_config_value(game, key="chat_use_history", value="AN")
    assert game["chat_use_history"] is True
    assert "AN" in result


def test_apply_chat_use_history_off():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_use_history", value="AUS")
    assert game["chat_use_history"] is False


def test_apply_chat_history_turns():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_history_turns", value="10")
    assert game["chat_history_turns"] == 10


def test_apply_chat_history_turns_invalid():
    game: dict = {}
    result = apply_ai_snake_config_value(game, key="chat_history_turns", value="abc")
    assert "zahl" in result.lower() or "fehler" in result.lower() or "erwartet" in result.lower()


def test_apply_chat_use_summary():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_use_summary", value="AUS")
    assert game["chat_use_summary"] is False


def test_apply_chat_summary_chars():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_summary_chars", value="2500")
    assert game["chat_summary_chars"] == 2500


def test_apply_chat_summary_update_every_turns():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_summary_update_every_turns", value="5")
    assert game["chat_summary_update_every_turns"] == 5


def test_apply_chat_pass_memory_to_worker():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_pass_memory_to_worker", value="AUS")
    assert game["chat_pass_memory_to_worker"] is False


def test_apply_chat_worker_mode():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_worker_mode", value="propose")
    assert game["chat_worker_mode"] == "propose"


def test_apply_chat_worker_mode_invalid():
    game: dict = {}
    result = apply_ai_snake_config_value(game, key="chat_worker_mode", value="unknown_mode")
    assert "snake_ask" in result or "erwartet" in result.lower() or "propose" in result


def test_apply_chat_backend_fallback():
    game: dict = {}
    apply_ai_snake_config_value(game, key="chat_backend_fallback", value="none")
    assert game["chat_backend_fallback"] == "none"


def test_apply_chat_backend_fallback_invalid():
    game: dict = {}
    result = apply_ai_snake_config_value(game, key="chat_backend_fallback", value="magic")
    assert "none" in result or "erwartet" in result.lower() or "lmstudio" in result


def test_history_default_value_is_true():
    items = ai_snake_config_items({})
    item = next(i for i in items if i["key"] == "chat_use_history")
    assert item["value"] is True


def test_summary_default_value_is_true():
    items = ai_snake_config_items({})
    item = next(i for i in items if i["key"] == "chat_use_summary")
    assert item["value"] is True


def test_pass_memory_default_value_is_true():
    items = ai_snake_config_items({})
    item = next(i for i in items if i["key"] == "chat_pass_memory_to_worker")
    assert item["value"] is True


def test_apply_preserves_other_settings():
    game = {"chat_backend": "lmstudio"}
    apply_ai_snake_config_value(game, key="chat_use_history", value="AUS")
    assert game["chat_backend"] == "lmstudio"
