from __future__ import annotations

import json


def test_ai_snake_config_writes_runtime_file_when_repo_seed_exists(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    seed = tmp_path / "user.json"
    seed.write_text(
        json.dumps({"schema_version": "user_config.v1", "settings": {"chat_backend": "seed"}}),
        encoding="utf-8",
    )
    before = seed.read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANANTA_USER_JSON", raising=False)

    from agent.routes import ai_snake_config as cfg

    cfg._save({"chat_backend": "runtime"})

    runtime = tmp_path / "data" / "user.json"
    assert seed.read_text(encoding="utf-8") == before
    assert runtime.exists()
    assert json.loads(runtime.read_text(encoding="utf-8"))["settings"]["chat_backend"] == "runtime"


def test_ai_snake_config_reads_seed_then_runtime_overrides(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "user.json").write_text(
        json.dumps({
            "schema_version": "user_config.v1",
            "settings": {"chat_backend": "seed", "chat_history_turns": 3},
        }),
        encoding="utf-8",
    )
    runtime = tmp_path / "data" / "user.json"
    runtime.parent.mkdir()
    runtime.write_text(
        json.dumps({"schema_version": "user_config.v1", "settings": {"chat_backend": "runtime"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANANTA_USER_JSON", raising=False)

    from agent.routes import ai_snake_config as cfg

    loaded = cfg._load()

    assert loaded["chat_backend"] == "runtime"
    assert loaded["chat_history_turns"] == 3
