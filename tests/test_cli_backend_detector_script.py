"""RED/GREEN test: scripts/check_cli_backend_shim_imports.py must exist and
report 0 import violations when the migration is complete.

Welle 3 goal: this detector is the gate-keeper. It greps the codebase for
``from agent.common.sgpt_`` imports (the legacy paths) and exits 1 if any
are found (because the shim layer should be gone by then).

Initially, the detector must exist as a script and report the current
state (with violations). Welle 3 T07 will then migrate all consumers to
the new ``agent.cli_backends.*`` paths, and the detector must exit 0.
Welle 3 T08 will then delete the shim layer entirely.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
DETECTOR_PATH = _REPO_ROOT / "scripts" / "check_cli_backend_shim_imports.py"


def test_detector_script_exists() -> None:
    """The detector script must exist at scripts/check_cli_backend_shim_imports.py."""
    assert DETECTOR_PATH.exists(), f"{DETECTOR_PATH} not found"


def test_detector_is_runnable() -> None:
    """The detector must be executable as a Python script (exit 0 or 1, not crash)."""
    result = subprocess.run(
        ["python", str(DETECTOR_PATH)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    # Detector must not crash — exit code 0 (clean) or 1 (violations) is OK
    assert result.returncode in (0, 1), (
        f"Detector exited with unexpected code {result.returncode}:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_detector_exit_code_zero_after_migration() -> None:
    """Welle-3-Goal: after all consumers migrate to agent.cli_backends.*,
    the detector must exit 0 (zero violations).

    This test asserts the Welle-3 end state. If the test fails, it means
    there are still legacy ``from agent.common.sgpt_`` imports somewhere
    in the codebase that need to be migrated.
    """
    result = subprocess.run(
        ["python", str(DETECTOR_PATH)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    # In Welle 3 final state: 0 violations → exit 0
    assert result.returncode == 0, (
        f"Detector found {result.stdout.count(chr(10))} violations. "
        f"Run the detector to see them:\n{result.stdout}"
    )
