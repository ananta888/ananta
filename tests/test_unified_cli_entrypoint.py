from __future__ import annotations

import importlib
from pathlib import Path


def test_unified_cli_main_is_importable() -> None:
    module = importlib.import_module("agent.cli.main")
    assert callable(module.main)


def test_console_script_points_to_unified_cli_main() -> None:
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'ananta = "agent.cli.main:main"' in pyproject_text
