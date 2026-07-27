from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validate_semantic_media_speech_track import validate_track

ROOT = Path(__file__).resolve().parents[2]
TRACK = ROOT / "todos/archiv/todo.ai-snake-semantic-media-speech-program.json"


def _payload() -> dict:
    return json.loads(TRACK.read_text(encoding="utf-8"))


def test_program_track_passes_schema_dag_milestone_and_summary_gate() -> None:
    assert validate_track(_payload(), track_path=TRACK, todos_dir=TRACK.parent) == []


@pytest.mark.parametrize(
    ("mutate", "reason_code", "pointer"),
    [
        (lambda payload: payload["tasks"].append(deepcopy(payload["tasks"][0])), "duplicate_task_id", "/tasks"),
        (
            lambda payload: payload["tasks"][0].update(depends_on=["UNKNOWN"]),
            "unknown_local_dependency",
            "/tasks/0/depends_on/0",
        ),
        (
            lambda payload: payload["tasks"][0].update(depends_on=["missing.json:T1"]),
            "unknown_cross_track_file",
            "/tasks/0/depends_on/0",
        ),
        (
            lambda payload: payload["milestones"][0]["task_ids"].remove("ASMP-BASE-001"),
            "task_milestone_membership_invalid",
            "/tasks/0/milestone_id",
        ),
        (
            lambda payload: payload["tasks_status_summary"].update(total=999),
            "derived_summary_mismatch",
            "/tasks_status_summary/total",
        ),
    ],
)
def test_gate_reports_stable_reason_and_json_pointer(mutate, reason_code: str, pointer: str) -> None:
    payload = _payload()
    mutate(payload)
    issues = validate_track(payload, track_path=TRACK, todos_dir=TRACK.parent)
    assert any(issue["reason_code"] == reason_code and issue["json_pointer"] == pointer for issue in issues)


def test_cli_returns_nonzero_and_json_for_invalid_track(tmp_path: Path) -> None:
    payload = _payload()
    payload["tasks"][0]["depends_on"] = ["unknown.json:T1"]
    invalid = tmp_path / TRACK.name
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_semantic_media_speech_track.py"), "--track", str(invalid)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    body = json.loads(result.stdout)
    assert result.returncode != 0
    assert body["ok"] is False
    assert body["issues"][0]["json_pointer"].startswith("/")
