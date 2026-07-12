import json

from agent.routes import ai_snake_config as config


def test_global_read_and_write_do_not_materialize_or_clear_session_override(tmp_path, monkeypatch):
    path = tmp_path / "user.json"
    path.write_text(
        json.dumps(
            {
                "chat_backend": "ananta-worker",
                "chat_active_session_id": "s1",
                "chat_sessions": [{"id": "s1", "settings_delta": {"chat_backend": "lmstudio"}}],
            }
        )
    )
    monkeypatch.setattr(config, "_user_json_path", lambda: path)
    monkeypatch.setattr(config, "_seed_user_json_path", lambda: None)
    assert config._current_config()["chat_backend"] == "ananta-worker"
    config._save({"chat_backend": "opencode"})
    stored = json.loads(path.read_text())
    effective_store = stored.get("settings", stored)
    assert effective_store["chat_backend"] == "opencode"
    sessions = effective_store.get("chat_sessions") or stored.get("chat_sessions")
    assert sessions[0]["settings_delta"]["chat_backend"] == "lmstudio"
