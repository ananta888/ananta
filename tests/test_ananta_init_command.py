from __future__ import annotations

import json
from pathlib import Path

from agent.cli import init_wizard
from agent.cli import main as cli_main


def test_ananta_init_dispatches_to_runtime_wizard(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_init_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(init_wizard, "main", fake_init_main)

    rc = cli_main.main(["init", "--yes", "--runtime-mode", "local-dev"])

    assert rc == 0
    assert captured["argv"] == ["--yes", "--runtime-mode", "local-dev"]


def test_ananta_init_non_interactive_local_dev_creates_profile(tmp_path: Path) -> None:
    profile_path = tmp_path / "runtime.profile.json"

    rc = cli_main.main(
        [
            "init",
            "--yes",
            "--runtime-mode",
            "local-dev",
            "--llm-backend",
            "ollama",
            "--model",
            "ananta-default",
            "--profile-path",
            str(profile_path),
            "--force",
        ]
    )

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["runtime_mode"] == "local-dev"
    assert payload["container_runtime"]["required"] is False
    assert payload["llm_backend"]["kind"] == "ollama"
