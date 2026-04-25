from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent.ananta_cli as ananta_cli
from agent.cli import init_wizard


def _args(**overrides) -> Namespace:
    base = {
        "runtime_mode": "auto",
        "llm_backend": None,
        "hardware_profile": "cpu-only",
        "endpoint_url": "",
        "model": "",
        "api_key_env": "OPENAI_API_KEY",
        "manual_json": "",
        "profile_path": "ananta.runtime-profile.json",
        "apply_config": False,
        "config_path": "config.json",
        "deployment_target": "none",
        "deployment_path": "ananta.deployment-profile.json",
        "backup_existing_deployment": True,
        "yes": True,
        "force": False,
    }
    base.update(overrides)
    return Namespace(**base)


def _fixed_now() -> datetime:
    return datetime(2026, 4, 25, 20, 30, tzinfo=timezone.utc)


def test_run_init_writes_local_dev_ollama_profile(tmp_path: Path) -> None:
    args = _args(
        runtime_mode="local-dev",
        llm_backend="ollama",
        model="llama3.1:8b",
        profile_path="runtime.profile.json",
        force=True,
    )

    result = init_wizard.run_init(
        args,
        cwd=tmp_path,
        output_fn=lambda _msg: None,
        now_fn=_fixed_now,
    )

    payload = json.loads((tmp_path / "runtime.profile.json").read_text(encoding="utf-8"))
    assert payload["runtime_mode"] == "local-dev"
    assert payload["hardware_profile"] == "cpu-only"
    assert payload["container_runtime"]["required"] is False
    assert payload["runtime_recommendation"]["limits"]["max_input_tokens"] == 8000
    assert payload["llm_backend"]["kind"] == "ollama"
    assert payload["config_patch"]["runtime_profile"] == "local-dev"
    assert payload["config_patch"]["default_provider"] == "ollama"
    assert payload["config_patch"]["default_model"] == "llama3.1:8b"
    assert result["config_path"] is None


def test_run_init_openai_compatible_applies_config_patch(tmp_path: Path) -> None:
    args = _args(
        runtime_mode="sandbox",
        llm_backend="openai-compatible",
        endpoint_url="http://127.0.0.1:1234",
        model="qwen2.5-coder:7b",
        profile_path="profile.json",
        apply_config=True,
        config_path="config.json",
        force=True,
    )

    init_wizard.run_init(
        args,
        cwd=tmp_path,
        output_fn=lambda _msg: None,
        now_fn=_fixed_now,
    )

    config_payload = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config_payload["runtime_profile"] == "compose-safe"
    assert config_payload["governance_mode"] == "balanced"
    assert config_payload["platform_mode"] == "trusted-internal"
    assert config_payload["default_provider"] == "local-openai"
    assert config_payload["default_model"] == "qwen2.5-coder:7b"
    backend_entry = config_payload["local_openai_backends"][0]
    assert backend_entry["id"] == "local-openai"
    assert backend_entry["base_url"] == "http://127.0.0.1:1234/v1"


def test_run_init_manual_backend_requires_json_in_yes_mode(tmp_path: Path) -> None:
    args = _args(
        runtime_mode="strict",
        llm_backend="manual",
        profile_path="profile.json",
        yes=True,
        force=True,
    )

    with pytest.raises(ValueError, match="manual backend requires --manual-json"):
        init_wizard.run_init(args, cwd=tmp_path, output_fn=lambda _msg: None)


def test_run_init_interactive_mode_prompts_for_values(tmp_path: Path) -> None:
    answers = iter(["strict", "lmstudio", "", "mistral-small"])
    args = _args(
        runtime_mode="auto",
        llm_backend=None,
        profile_path="profile.json",
        yes=False,
        force=True,
    )

    init_wizard.run_init(
        args,
        cwd=tmp_path,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _msg: None,
        env={},
        docker_env_exists=False,
        now_fn=_fixed_now,
    )

    payload = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert payload["runtime_mode"] == "strict"
    assert payload["llm_backend"]["kind"] == "lmstudio"
    assert payload["llm_backend"]["endpoint_url"] == "http://localhost:1234/v1"
    assert payload["llm_backend"]["model"] == "mistral-small"


def test_ananta_cli_dispatches_init_to_wizard(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def _fake_init_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(init_wizard, "main", _fake_init_main)

    rc = ananta_cli.main(["init", "--yes", "--runtime-mode", "local-dev"])

    assert rc == 0
    assert captured["argv"] == ["--yes", "--runtime-mode", "local-dev"]


def test_run_init_generates_deployment_profile_and_backup(tmp_path: Path) -> None:
    existing = tmp_path / "deploy.profile.json"
    existing.write_text('{"old": true}\n', encoding="utf-8")
    args = _args(
        runtime_mode="sandbox",
        llm_backend="ollama",
        deployment_target="docker-compose",
        deployment_path="deploy.profile.json",
        force=False,
    )

    result = init_wizard.run_init(
        args,
        cwd=tmp_path,
        output_fn=lambda _msg: None,
        now_fn=_fixed_now,
    )

    deployment_payload = json.loads((tmp_path / "deploy.profile.json").read_text(encoding="utf-8"))
    assert deployment_payload["target"] == "docker-compose"
    assert deployment_payload["isolation_level"] == "stronger"
    assert result["deployment_profile_path"] is not None
    assert result["deployment_backup_path"] is not None
    assert Path(result["deployment_backup_path"]).exists()
