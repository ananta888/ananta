from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "operator_tui_visual_smoke.py"


def test_visual_smoke_capabilities_only_outputs_json() -> None:
    result = subprocess.run(
        [sys.executable, str(_script_path()), "--capabilities-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    first_line = (result.stdout or "").splitlines()[0]
    payload = json.loads(first_line)
    assert "preferred_adapter" in payload
    assert payload.get("ansi") is True


def test_visual_smoke_reports_fallback_suggestion_for_unsupported_adapter() -> None:
    env = dict(os.environ)
    env["TERM"] = "dumb"
    env.pop("KITTY_WINDOW_ID", None)
    result = subprocess.run(
        [sys.executable, str(_script_path()), "--adapter", "kitty", "--width", "20", "--height", "5", "--fps", "10"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2
    assert "try --adapter ansi" in (result.stderr or "")
