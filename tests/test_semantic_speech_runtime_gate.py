from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_semantic_speech_runtime_gate_artifact_is_current_and_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_semantic_speech_runtime_gate.py", "--verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["passed"] is True
    assert report["contains_media_or_transcript_data"] is False
    assert report["metrics"]["simulated_duration_hours"] == 8
    assert report["metrics"]["correction_attempts"] == 480
