"""The complexity-suppression detector must exist, run clean and flag regressions.

The project keeps every function under the configured McCabe limit; a bare
``# noqa: C901`` hides a complexity regression instead of fixing it. This
suite pins the detector plus one positive and one negative detection case.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import check_complexity_suppressions as detector

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "check_complexity_suppressions.py"


def test_detector_script_exists() -> None:
    assert _SCRIPT.exists(), f"{_SCRIPT} not found"


def test_detector_runs_clean_on_repository() -> None:
    """The repository carries no ``# noqa: C901`` suppressions."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_detector_flags_suppression(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "module.py"
    source.write_text("def f():\n    return 1  # noqa: C901\n", encoding="utf-8")
    monkeypatch.setattr(detector, "_tracked_python_files", lambda root: [source])
    monkeypatch.setattr(detector, "ALLOWED_SUPPRESSIONS", frozenset())

    assert detector.find_suppressions(tmp_path) == [
        ("module.py", 2, "return 1  # noqa: C901"),
    ]


def test_detector_accepts_clean_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "module.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(detector, "_tracked_python_files", lambda root: [source])

    assert detector.find_suppressions(tmp_path) == []


def test_detector_honours_allowlist(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "module.py"
    source.write_text("def f():\n    return 1  # noqa: C901\n", encoding="utf-8")
    monkeypatch.setattr(detector, "_tracked_python_files", lambda root: [source])
    monkeypatch.setattr(detector, "ALLOWED_SUPPRESSIONS", frozenset({"module.py"}))

    assert detector.find_suppressions(tmp_path) == []
