from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path

from tests.workflow_runtime.release_gate.test_release_gate import _gate_and_evidence

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run-workflow-runtime-release-gate.py"


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_workspace_revision_binds_tracked_and_untracked_content_but_not_output(
    tmp_path: Path,
) -> None:
    module = runpy.run_path(str(SCRIPT))
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@ananta.invalid")
    _git(repository, "config", "user.name", "Ananta Tests")
    tracked = repository / "tracked.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "tracked.py")
    _git(repository, "commit", "-q", "-m", "test: seed")

    clean_revision = module["_workspace_revision"](repository)
    tracked.write_text("VALUE = 2\n", encoding="utf-8")
    dirty_revision = module["_workspace_revision"](repository)
    untracked = repository / "new.py"
    untracked.write_text("NEW = True\n", encoding="utf-8")
    untracked_revision = module["_workspace_revision"](repository)
    output = repository / "release.json"
    output.write_text('{"volatile":true}\n', encoding="utf-8")

    assert "+worktree." not in clean_revision
    assert dirty_revision != clean_revision
    assert untracked_revision != dirty_revision
    assert module["_workspace_revision"](
        repository,
        excluded_path=output,
    ) == untracked_revision


def test_no_commands_mode_is_explicitly_non_releasable(tmp_path: Path, capsys) -> None:
    module = runpy.run_path(str(SCRIPT))
    output = tmp_path / "gate.json"
    evidence_input = tmp_path / "evidence.json"
    gate, evidence = _gate_and_evidence()
    evidence_input.write_text(
        json.dumps(
            {
                "schema": "ananta.workflow_runtime_release_input.v1",
                "evidence_version": "2.0.0",
                "contract_hash": gate.contract_hash,
                "records": [item.to_dict() for item in evidence],
            }
        ),
        encoding="utf-8",
    )

    returncode = module["main"](
        [
            "--no-commands",
            "--evidence-input",
            str(evidence_input),
            "--output",
            str(output),
        ]
    )

    assert returncode == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["status"] == "failed"
    assert any(item["code"] == "verification_command_missing" for item in artifact["deviations"])
    assert str(tmp_path) not in output.read_text(encoding="utf-8")
    assert json.loads(capsys.readouterr().out) == artifact
