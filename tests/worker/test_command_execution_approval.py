from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from worker.shell.command_executor import execute_command_plan


POLICY = {
    "allowlist": ["echo", "python", "pytest"],
    "approval_required_commands": ["pip", "npm"],
    "denylist_tokens": ["rm -rf /", "mkfs"],
}


def test_denied_command_is_blocked_before_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = {"value": False}

    def _unexpected_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called["value"] = True
        raise AssertionError("subprocess.run should not be called for denied command")

    monkeypatch.setattr("worker.shell.command_executor.subprocess.run", _unexpected_run)
    with pytest.raises(PermissionError, match="policy_denied"):
        execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact={
                "command": "rm -rf /",
                "working_directory": ".",
                "required_approval": True,
            },
            task_id="T1",
            capability_id="worker.command.execute",
            context_hash="ctx-1",
            shell_policy=POLICY,
            hub_policy_decision="allow",
        )
    assert called["value"] is False


def test_approval_required_command_rejects_missing_approval(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="approval_required"):
        execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact={
                "command": "pip --version",
                "working_directory": ".",
                "required_approval": True,
            },
            task_id="T1",
            capability_id="worker.command.execute",
            context_hash="ctx-1",
            shell_policy=POLICY,
            hub_policy_decision="allow",
        )


def test_command_execution_with_matching_approval_succeeds(tmp_path: Path) -> None:
    command = "python -c \"print('ok')\""
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    artifact = execute_command_plan(
        repository_root=tmp_path,
        command_plan_artifact={
            "command": command,
            "working_directory": ".",
            "required_approval": False,
        },
        task_id="T1",
        capability_id="worker.command.execute",
        context_hash="ctx-1",
        shell_policy=POLICY,
        hub_policy_decision="approval_required",
        approval={
            "status": "approved",
            "task_id": "T1",
            "capability_id": "worker.command.execute",
            "context_hash": "ctx-1",
            "command_hash": command_hash,
        },
    )
    assert artifact["schema"] == "test_result_artifact.v1"
    assert artifact["status"] == "passed"
    assert artifact["exit_code"] == 0
    assert "duration_ms" in artifact["output_summary"]
    assert "ok" in artifact["stdout_ref"]


def test_command_execution_rejects_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="working_directory_outside_workspace"):
        execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact={
                "command": "echo hi",
                "working_directory": "../",
                "required_approval": False,
            },
            task_id="T1",
            capability_id="worker.command.execute",
            context_hash="ctx-1",
            shell_policy=POLICY,
            hub_policy_decision="allow",
        )
