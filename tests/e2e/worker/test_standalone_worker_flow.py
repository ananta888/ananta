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


def test_standalone_worker_flow_supports_todo_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "todo-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "worker_todo_contract.v1",
                "task_id": "AW-TODO-37",
                "goal_id": "G-37",
                "trace_id": "tr-todo-37",
                "worker": {
                    "executor_kind": "ananta_worker",
                    "worker_profile": "balanced",
                    "profile_source": "task_context",
                },
                "todo": {
                    "version": "1.0",
                    "track": "worker-subplan",
                    "tasks": [
                        {
                            "id": "todo-1",
                            "title": "Implement change",
                            "instructions": "Apply requested change and return patch artifact.",
                            "status": "todo",
                            "expected_artifacts": [{"kind": "patch_artifact", "required": True}],
                            "acceptance_criteria": ["Patch artifact exists"],
                        }
                    ],
                },
                "execution": {"mode": "assistant_execute", "runner_prompt": "Execute todo contract."},
                "control_manifest": {
                    "trace_id": "tr-todo-37",
                    "capability_id": "worker.command.execute",
                    "context_hash": "ctx-todo-37",
                },
                "expected_result_schema": "worker_todo_result.v1",
            }
        ),
        encoding="utf-8",
    )
    payload = run_cli(manifest_path=str(manifest), workspace_dir=str(workspace))
    assert payload["result"]["schema"] == "worker_todo_result.v1"
    assert payload["result"]["task_id"] == "AW-TODO-37"
    assert payload["result"]["summary"]["completed_items"] == 1
