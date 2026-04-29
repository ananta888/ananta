from __future__ import annotations

import json
from pathlib import Path

from worker.cli.standalone_worker_cli import run_cli


def test_standalone_worker_flow_emits_machine_readable_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "standalone_task_contract.v1",
                "task_id": "AW-T37",
                "goal": "bootstrap project",
                "command": "echo scaffold",
                "worker_profile": "balanced",
                "files": ["README.md"],
                "diffs": [],
                "control_manifest": {"trace_id": "tr-37", "capability_id": "worker.command.execute", "context_hash": "ctx-37"},
            }
        ),
        encoding="utf-8",
    )
    payload = run_cli(manifest_path=str(manifest), workspace_dir=str(workspace))
    assert payload["result"]["status"] == "completed"
    assert payload["result"]["task_id"] == "AW-T37"
    assert payload["artifacts"]
    assert payload["trace_events"]

